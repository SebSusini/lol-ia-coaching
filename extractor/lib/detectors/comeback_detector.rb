# Detects momentum shifts — when the gold diff changes sign
# (player goes from behind to ahead or vice versa).
# Uses gold data from timeline frames (participantFrames).

class ComebackDetector
  MIN_GOLD_SWING = 500 # Minimum gold swing to count as a real shift

  def initialize(context)
    @context = context
  end

  def detect
    enemy_laner = find_enemy_laner
    return [] unless enemy_laner

    enemy_id = enemy_laner["participantId"]

    # Build gold curve from timeline frames
    gold_diffs = @context.timeline_frames.filter_map do |frame|
      ts_ms = frame["timestamp"]
      ts_sec = ts_ms / 1000.0
      next if ts_sec < 60 # Skip first minute

      my_pf = frame.dig("participantFrames", @context.my_participant_id.to_s)
      enemy_pf = frame.dig("participantFrames", enemy_id.to_s)
      next unless my_pf && enemy_pf

      {
        time_seconds: ts_sec,
        gold_diff: my_pf["totalGold"] - enemy_pf["totalGold"]
      }
    end

    return [] if gold_diffs.size < 2

    shifts = []

    gold_diffs.each_cons(2) do |prev, curr|
      # Detect sign change (excluding zero-to-something transitions unless significant)
      prev_sign = prev[:gold_diff] <=> 0
      curr_sign = curr[:gold_diff] <=> 0

      next if prev_sign == curr_sign
      next if prev_sign == 0 || curr_sign == 0

      # Must be a significant swing
      swing = (curr[:gold_diff] - prev[:gold_diff]).abs
      next if swing < MIN_GOLD_SWING

      direction = curr[:gold_diff] > 0 ? "COMEBACK" : "FALLING_BEHIND"
      trigger = find_trigger(prev[:time_seconds], curr[:time_seconds])

      shifts << {
        time_seconds: curr[:time_seconds],
        time_formatted: format_time(curr[:time_seconds]),
        type: "MOMENTUM_SHIFT",
        direction: direction,
        gold_diff_before: prev[:gold_diff],
        gold_diff_after: curr[:gold_diff],
        trigger: trigger
      }
    end

    shifts
  end

  private

  def find_enemy_laner
    my = @context.participants.find { |p| p["participantId"] == @context.my_participant_id }
    return nil unless my
    @context.participants.find do |p|
      p["teamPosition"] == my["teamPosition"] && p["teamId"] != my["teamId"]
    end
  end

  def find_trigger(start_sec, end_sec)
    start_ms = (start_sec * 1000).to_i
    end_ms = (end_sec * 1000).to_i
    my_team_id = @context.participants.find { |p| p["participantId"] == @context.my_participant_id }&.dig("teamId")

    # Look for kills in this window
    kills = @context.timeline_events.select do |e|
      e["type"] == "CHAMPION_KILL" && e["timestamp"].between?(start_ms, end_ms)
    end

    your_team_kills = kills.count do |e|
      killer = @context.participants.find { |p| p["participantId"] == e["killerId"] }
      killer&.dig("teamId") == my_team_id
    end

    enemy_team_kills = kills.size - your_team_kills

    # Look for objectives
    objectives = @context.timeline_events.select do |e|
      e["type"] == "ELITE_MONSTER_KILL" && e["timestamp"].between?(start_ms, end_ms)
    end

    # Look for tower kills
    towers = @context.timeline_events.select do |e|
      e["type"] == "BUILDING_KILL" && e["timestamp"].between?(start_ms, end_ms)
    end

    parts = []
    window_min = ((end_sec - start_sec) / 60.0).round(0).to_i
    window_min = [window_min, 1].max

    if your_team_kills > 0
      parts << "#{your_team_kills} kill#{your_team_kills > 1 ? 's' : ''}"
    end
    if enemy_team_kills > 0
      parts << "#{enemy_team_kills} death#{enemy_team_kills > 1 ? 's' : ''}"
    end
    if objectives.any?
      obj_names = objectives.map { |o| format_monster(o["monsterType"]) }
      parts << obj_names.join(" + ")
    end
    if towers.any?
      parts << "#{towers.size} tower#{towers.size > 1 ? 's' : ''}"
    end

    if parts.any?
      parts.join(", ") + " in #{window_min} min"
    else
      "gradual gold change"
    end
  end

  def format_monster(type)
    case type
    when "DRAGON" then "Dragon"
    when "BARON_NASHOR" then "Baron"
    when "RIFTHERALD" then "Herald"
    when "HORDE" then "Voidgrubs"
    else type || "objective"
    end
  end

  def format_time(seconds)
    minutes = (seconds / 60).to_i
    secs = (seconds % 60).to_i
    "%d:%02d" % [minutes, secs]
  end
end
