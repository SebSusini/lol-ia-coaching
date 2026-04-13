require "json"

class ReviewFormatter
  def initialize(context, timeline_events, item_resolver = nil, positions = nil)
    @context = context
    @timeline_events = timeline_events
    @item_resolver = item_resolver
    @positions = positions || []
  end

  def format
    {
      meta: build_meta,
      final_stats: build_final_stats,
      timeline: @timeline_events,
      patterns: build_patterns,
      gold_curve: build_gold_curve,
      position_map: build_position_map
    }
  end

  def to_json
    JSON.pretty_generate(format)
  end

  private

  def build_meta
    my_participant = find_my_participant
    enemy_laner = find_enemy_laner
    my_team_id = my_participant&.dig("teamId")

    {
      match_id: @context.match_id,
      champion: @context.my_champion,
      role: my_participant&.dig("teamPosition"),
      enemy_laner: {
        champion: enemy_laner&.dig("championName"),
        kda: enemy_laner ? "#{enemy_laner['kills']}/#{enemy_laner['deaths']}/#{enemy_laner['assists']}" : nil
      },
      result: @context.result,
      game_version: @context.game_version,
      duration_seconds: @context.game_duration_seconds,
      team: @context.my_team,
      teammates: @context.participants
        .select { |p| p["teamId"] == my_team_id && p["participantId"] != @context.my_participant_id }
        .map { |p| { champion: p["championName"], position: p["teamPosition"], kda: "#{p['kills']}/#{p['deaths']}/#{p['assists']}" } },
      enemies: @context.participants
        .select { |p| p["teamId"] != my_team_id }
        .map { |p| { champion: p["championName"], position: p["teamPosition"], kda: "#{p['kills']}/#{p['deaths']}/#{p['assists']}" } }
    }
  end

  def build_final_stats
    p = find_my_participant
    return {} unless p

    total_cs = p["totalMinionsKilled"] + p["neutralMinionsKilled"]
    team_kills = @context.participants.select { |t| t["teamId"] == p["teamId"] }.sum { |t| t["kills"] }

    {
      kda: "#{p['kills']}/#{p['deaths']}/#{p['assists']}",
      cs: total_cs,
      cs_per_min: (total_cs.to_f / (@context.game_duration_seconds / 60.0)).round(1),
      gold: p["goldEarned"],
      damage_to_champions: p["totalDamageDealtToChampions"],
      damage_taken: p["totalDamageTaken"],
      vision_score: p["visionScore"],
      kill_participation: team_kills > 0 ? ((p["kills"] + p["assists"]).to_f / team_kills * 100).round(0) : 0,
      items_final: resolve_items((0..6).map { |i| p["item#{i}"] }.reject { |i| i == 0 })
    }
  end

  def build_patterns
    deaths = @timeline_events.select { |e| e[:type] == "DEATH" }
    roams = @timeline_events.select { |e| e[:type] == "ROAM" }
    teamfights = @timeline_events.select { |e| e[:type] == "TEAMFIGHT" }
    objectives = @timeline_events.select { |e| e[:type] == "OBJECTIVE" }

    enemy_laner = find_enemy_laner
    enemy_jungler = @context.participants.find { |p| p["teamPosition"] == "JUNGLE" && p["teamId"] != find_my_participant&.dig("teamId") }

    {
      death_count: deaths.size,
      death_timings: deaths.map { |d| d[:time_formatted] },
      gold_unspent_at_deaths: deaths.map { |d| d[:gold_unspent] }.compact,
      deaths_by_zone: deaths.group_by { |d| d[:zone] || "UNKNOWN" }.transform_values(&:size),
      deaths_to_enemy_laner: deaths.count { |d| d[:killed_by] == enemy_laner&.dig("championName") },
      deaths_with_enemy_jungler: deaths.count { |d| (d[:assisted_by] || []).include?(enemy_jungler&.dig("championName")) || d[:killed_by] == enemy_jungler&.dig("championName") },
      solo_deaths: deaths.count { |d| (d[:assisted_by] || []).empty? },
      teamfights_participated: teamfights.count { |t| t[:you_participated] },
      teamfights_total: teamfights.size,
      teamfights_won: teamfights.count { |t| t[:result] == "WON" },
      objectives_your_team: objectives.count { |o| o[:taken_by] == "YOUR_TEAM" },
      objectives_enemy: objectives.count { |o| o[:taken_by] == "ENEMY_TEAM" },
      roams_attempted: roams.size,
      roams_successful: roams.count { |r| r[:result] == "KILL" },
      # V4 patterns
      jungler_ganks_near_you: build_jungler_pattern,
      vision_score_rating: build_vision_rating,
      item_spike_delay: build_item_spike_delay,
      momentum_shifts: @timeline_events.count { |e| e[:type] == "MOMENTUM_SHIFT" },
      damage_efficiency_verdict: build_damage_verdict
    }
  end

  def build_gold_curve
    @context.timeline_frames.filter_map do |frame|
      ts_min = frame["timestamp"] / 60000.0
      next unless ts_min > 0 && (ts_min % 5).abs < 0.1 # Every 5 min

      my_frame = frame.dig("participantFrames", @context.my_participant_id.to_s)
      enemy_id = find_enemy_laner&.dig("participantId")
      enemy_frame = frame.dig("participantFrames", enemy_id.to_s) if enemy_id

      next unless my_frame

      my_cs = (my_frame["minionsKilled"] || 0) + (my_frame["jungleMinionsKilled"] || 0)
      enemy_cs = enemy_frame ? (enemy_frame["minionsKilled"] || 0) + (enemy_frame["jungleMinionsKilled"] || 0) : nil

      {
        time_min: ts_min.round(0).to_i,
        your_gold: my_frame["totalGold"],
        enemy_gold: enemy_frame&.dig("totalGold"),
        gold_diff: enemy_frame ? my_frame["totalGold"] - enemy_frame["totalGold"] : nil,
        your_cs: my_cs,
        enemy_cs: enemy_cs,
        your_level: my_frame["level"],
        enemy_level: enemy_frame&.dig("level")
      }
    end
  end

  def find_my_participant
    @context.participants.find { |p| p["participantId"] == @context.my_participant_id }
  end

  def find_enemy_laner
    my = find_my_participant
    return nil unless my
    @context.participants.find do |p|
      p["teamPosition"] == my["teamPosition"] && p["teamId"] != my["teamId"]
    end
  end

  def build_jungler_pattern
    jungler_events = @timeline_events.select { |e| e[:type] == "JUNGLER_POSITION" && e[:near_you] }
    jungler_events.size
  end

  def build_vision_rating
    p = find_my_participant
    return "unknown" unless p

    vision_score = p["visionScore"] || 0
    game_minutes = @context.game_duration_seconds / 60.0

    # Vision score per minute is the standard metric
    vs_per_min = vision_score / game_minutes

    if vs_per_min >= 1.5
      "good"
    elsif vs_per_min >= 0.8
      "average"
    else
      "poor"
    end
  end

  def build_item_spike_delay
    item_spikes = @timeline_events.select { |e| e[:type] == "ITEM_SPIKE" && e[:who] == "YOU" && e[:time_advantage] }
    return nil if item_spikes.empty?

    avg_delay = item_spikes.sum { |s| s[:time_advantage] } / item_spikes.size.to_f
    avg_delay.round(0)
  end

  def build_damage_verdict
    dmg_event = @timeline_events.find { |e| e[:type] == "DAMAGE_EFFICIENCY" }
    dmg_event ? dmg_event[:verdict] : nil
  end

  def build_position_map
    @positions.map do |p|
      { time_min: p[:time_min], zone: p[:zone], x: p[:x], y: p[:y] }
    end
  end

  def resolve_items(item_ids)
    if @item_resolver
      item_ids.map { |id| @item_resolver.resolve(id) }.compact
    else
      item_ids
    end
  end
end
