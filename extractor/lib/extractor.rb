require "json"

require_relative "game_context"
require_relative "filters/mid_lane_filter"
require_relative "detectors/death_detector"
require_relative "detectors/cs_state_detector"
require_relative "detectors/roam_detector"
require_relative "formatters/review_formatter"

class Extractor
  def initialize(timeline_path:, summoner_name:, positions_path: nil)
    @positions_data = positions_path ? JSON.parse(File.read(positions_path)) : { "players_state" => [], "wards" => [] }
    @timeline_data = JSON.parse(File.read(timeline_path))
    @summoner_name = summoner_name
  end

  def extract
    context = GameContext.new(@positions_data, @timeline_data, @summoner_name)

    # Filter events relevant to mid lane
    filtered = MidLaneFilter.new(context).filter

    # Run detectors
    timeline_events = []
    timeline_events += DeathDetector.new(context, filtered).detect
    timeline_events += CsStateDetector.new(context, filtered).detect
    timeline_events += RoamDetector.new(context, filtered).detect

    # Sort by time
    timeline_events.sort_by! { |e| e[:time_seconds] }

    # Format output
    ReviewFormatter.new(context, timeline_events).format
  end
end
