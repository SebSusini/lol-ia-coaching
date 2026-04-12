require "json"

require_relative "game_context"
require_relative "item_resolver"
require_relative "filters/mid_lane_filter"
require_relative "detectors/death_detector"
require_relative "detectors/death_position_classifier"
require_relative "detectors/cs_state_detector"
require_relative "detectors/roam_detector"
require_relative "detectors/teamfight_detector"
require_relative "detectors/objective_detector"
require_relative "detectors/position_tracker"
require_relative "detectors/jungler_tracker"
require_relative "detectors/ward_tracker"
require_relative "detectors/item_spike_detector"
require_relative "detectors/lane_state_detector"
require_relative "detectors/comeback_detector"
require_relative "detectors/damage_efficiency_detector"
require_relative "formatters/review_formatter"

class Extractor
  def initialize(timeline_path:, summoner_name:, positions_path: nil, live_path: nil)
    @positions_data = positions_path ? JSON.parse(File.read(positions_path)) : { "players_state" => [], "wards" => [] }
    @timeline_data = JSON.parse(File.read(timeline_path))
    @live_data = live_path ? JSON.parse(File.read(live_path)) : nil
    @summoner_name = summoner_name
    @item_resolver = ItemResolver.new
  end

  def extract
    context = GameContext.new(@positions_data, @timeline_data, @summoner_name)
    filtered = MidLaneFilter.new(context).filter

    # Run all detectors
    timeline_events = []
    timeline_events += DeathDetector.new(context, filtered).detect
    timeline_events += CsStateDetector.new(context, filtered).detect
    timeline_events += RoamDetector.new(context, filtered).detect
    timeline_events += TeamfightDetector.new(context, filtered).detect
    timeline_events += ObjectiveDetector.new(context, filtered).detect

    # Run new detectors (V4)
    timeline_events += JunglerTracker.new(context).detect
    timeline_events += WardTracker.new(context).detect
    timeline_events += ItemSpikeDetector.new(context, @item_resolver).detect
    timeline_events += LaneStateDetector.new(context).detect
    timeline_events += ComebackDetector.new(context).detect
    timeline_events += DamageEfficiencyDetector.new(context).detect

    # Enrich deaths with position classification
    classifier = DeathPositionClassifier.new(context.my_team || "Blue")
    timeline_events.each do |event|
      if event[:type] == "DEATH" && event[:position]
        classification = classifier.classify(event[:position])
        event[:zone] = classification[:zone]
        event[:zone_detail] = classification[:detail]
        event[:evitable] = classification[:evitable]
      end
    end

    # Enrich deaths with item comparison from live data
    if @live_data
      enrich_with_live_data(context, timeline_events)
    end

    # Track positions
    tracker = PositionTracker.new(context)
    my_positions = tracker.player_positions(context.my_participant_id)

    # Enrich deaths with nearby player positions
    timeline_events.select { |e| e[:type] == "DEATH" }.each do |death|
      minute = (death[:time_seconds] / 60.0).round(0)
      all_pos = tracker.all_positions_at(minute)
      death[:nearby_enemies] = all_pos.select { |p| p[:team] != context.my_team }
        .map { |p| { champion: p[:champion], zone: p[:zone] } }
    end

    # Enrich objectives with player presence
    timeline_events.select { |e| e[:type] == "OBJECTIVE" }.each do |obj|
      minute = (obj[:time_seconds] / 60.0).round(0)
      my_pos = my_positions.find { |p| p[:time_min].round(0) == minute }
      if my_pos
        obj[:your_zone] = my_pos[:zone]
        obj[:you_present] = tracker.near_objective?(my_pos[:x], my_pos[:y], obj[:monster])
      end
    end

    # Sort by time
    timeline_events.sort_by! { |e| e[:time_seconds] }

    # Format output
    ReviewFormatter.new(context, timeline_events, @item_resolver, my_positions).format
  end

  private

  def enrich_with_live_data(context, events)
    snapshots = @live_data["snapshots"] || []
    return if snapshots.empty?

    events.select { |e| e[:type] == "DEATH" }.each do |death|
      # Find closest snapshot to death time
      closest = snapshots.min_by { |s| (s["gameTime"] - death[:time_seconds]).abs }
      next unless closest

      my_player = closest["players"]&.find { |p| p["champion"] == context.my_champion }
      enemy_laner = closest["players"]&.find { |p| p["champion"] == context.enemy_mid_champion }

      if my_player
        death[:your_items] = (my_player["items"] || []).map { |i| i["name"] }.reject(&:nil?)
        death[:your_level_at_death] = my_player["level"]
      end

      if enemy_laner
        death[:enemy_items] = (enemy_laner["items"] || []).map { |i| i["name"] }.reject(&:nil?)
        death[:enemy_level_at_death] = enemy_laner["level"]
      end
    end
  end
end
