class RoamDetector
  MID_LANE_CENTER_X = 7500
  MID_LANE_CENTER_Y = 7500
  ROAM_DISTANCE_THRESHOLD = 3500 # units away from mid center to count as roam

  def initialize(context, filtered_data)
    @context = context
    @filtered_data = filtered_data
  end

  def detect
    return [] unless @context.positions_data["players_state"]

    roams = []
    in_roam = false
    roam_start = nil
    roam_start_pos = nil

    @context.positions_data["players_state"].each do |state|
      my_player = state["players"]&.find { |p| p["champ"] == @context.my_champion }
      next unless my_player

      pos = my_player["pos"]
      timestamp = state["timestamp"]
      distance_from_mid = Math.sqrt(
        (pos[0] - MID_LANE_CENTER_X) ** 2 + (pos[1] - MID_LANE_CENTER_Y) ** 2
      )

      if distance_from_mid > ROAM_DISTANCE_THRESHOLD && !in_roam
        in_roam = true
        roam_start = timestamp
        roam_start_pos = pos
      elsif distance_from_mid <= ROAM_DISTANCE_THRESHOLD && in_roam
        in_roam = false

        # Only count roams longer than 10 seconds (ignore brief lane movements)
        duration = timestamp - roam_start
        if duration > 10
          destination = determine_destination(roam_start_pos)
          roam_result = find_roam_result(roam_start, timestamp)

          roams << {
            time_seconds: roam_start,
            time_formatted: format_time(roam_start),
            type: "ROAM",
            destination: destination,
            duration_seconds: duration.round(1),
            result: roam_result
          }
        end
      end
    end

    roams
  end

  private

  def determine_destination(pos)
    x, y = pos

    if y > 10000
      "TOP"
    elsif y < 5000
      "BOT"
    elsif x < 5000
      y > 7500 ? "TOP_JUNGLE" : "BOT_JUNGLE"
    elsif x > 10000
      y > 7500 ? "TOP_JUNGLE" : "BOT_JUNGLE"
    else
      "RIVER"
    end
  end

  def find_roam_result(start_time, end_time)
    start_ms = (start_time * 1000).to_i
    end_ms = (end_time * 1000).to_i

    kills_during = @context.timeline_events.select do |e|
      e["type"] == "CHAMPION_KILL" &&
        e["timestamp"].between?(start_ms, end_ms) &&
        (e["killerId"] == @context.my_participant_id ||
         (e["assistingParticipantIds"] || []).include?(@context.my_participant_id))
    end

    if kills_during.any?
      "KILL"
    else
      "NO_KILL"
    end
  end

  def format_time(seconds)
    minutes = (seconds / 60).to_i
    secs = (seconds % 60).to_i
    "%d:%02d" % [minutes, secs]
  end
end
