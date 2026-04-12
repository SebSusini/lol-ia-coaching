# At each death, calculates the exact gold/CS/level diff vs lane opponent.
# Provides a state assessment (AHEAD, EVEN, BEHIND, FAR_BEHIND).

class LaneStateDetector
  FAR_BEHIND_GOLD_DIFF = -1500
  BEHIND_GOLD_DIFF = -500
  AHEAD_GOLD_DIFF = 500

  def initialize(context)
    @context = context
  end

  def detect
    enemy_laner = find_enemy_laner
    return [] unless enemy_laner

    enemy_id = enemy_laner["participantId"]

    # Find all death events for the player
    death_events = @context.timeline_events.select do |e|
      e["type"] == "CHAMPION_KILL" && e["victimId"] == @context.my_participant_id
    end

    death_events.filter_map do |event|
      ts_ms = event["timestamp"]
      ts_sec = ts_ms / 1000.0

      frame = nearest_frame(ts_ms)
      next unless frame

      my_pf = frame.dig("participantFrames", @context.my_participant_id.to_s)
      enemy_pf = frame.dig("participantFrames", enemy_id.to_s)
      next unless my_pf && enemy_pf

      my_gold = my_pf["totalGold"]
      enemy_gold = enemy_pf["totalGold"]
      gold_diff = my_gold - enemy_gold

      my_cs = (my_pf["minionsKilled"] || 0) + (my_pf["jungleMinionsKilled"] || 0)
      enemy_cs = (enemy_pf["minionsKilled"] || 0) + (enemy_pf["jungleMinionsKilled"] || 0)

      my_level = my_pf["level"]
      enemy_level = enemy_pf["level"]

      state = assess_state(gold_diff)

      {
        time_seconds: ts_sec,
        time_formatted: format_time(ts_sec),
        type: "LANE_STATE_AT_DEATH",
        your_gold: my_gold,
        enemy_gold: enemy_gold,
        gold_diff: gold_diff,
        your_cs: my_cs,
        enemy_cs: enemy_cs,
        cs_diff: my_cs - enemy_cs,
        your_level: my_level,
        enemy_level: enemy_level,
        state: state
      }
    end
  end

  private

  def find_enemy_laner
    my = @context.participants.find { |p| p["participantId"] == @context.my_participant_id }
    return nil unless my
    @context.participants.find do |p|
      p["teamPosition"] == my["teamPosition"] && p["teamId"] != my["teamId"]
    end
  end

  def nearest_frame(timestamp_ms)
    @context.timeline_frames.min_by { |f| (f["timestamp"] - timestamp_ms).abs }
  end

  def assess_state(gold_diff)
    if gold_diff <= FAR_BEHIND_GOLD_DIFF
      "FAR_BEHIND"
    elsif gold_diff <= BEHIND_GOLD_DIFF
      "BEHIND"
    elsif gold_diff >= AHEAD_GOLD_DIFF
      "AHEAD"
    else
      "EVEN"
    end
  end

  def format_time(seconds)
    minutes = (seconds / 60).to_i
    secs = (seconds % 60).to_i
    "%d:%02d" % [minutes, secs]
  end
end
