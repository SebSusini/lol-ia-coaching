class CsStateDetector
  SNAPSHOT_INTERVALS = [5, 10, 15, 20, 25] # minutes

  def initialize(context, filtered_data)
    @context = context
    @filtered_data = filtered_data
  end

  def detect
    SNAPSHOT_INTERVALS.filter_map do |minute|
      timestamp_ms = minute * 60 * 1000
      frame = find_frame_at(timestamp_ms)
      next unless frame

      my_frame = frame.dig("participantFrames", @context.my_participant_id.to_s)
      enemy_mid_id = find_enemy_mid_participant_id
      enemy_frame = frame.dig("participantFrames", enemy_mid_id.to_s) if enemy_mid_id

      next unless my_frame

      my_cs = (my_frame["minionsKilled"] || 0) + (my_frame["jungleMinionsKilled"] || 0)
      enemy_cs = if enemy_frame
        (enemy_frame["minionsKilled"] || 0) + (enemy_frame["jungleMinionsKilled"] || 0)
      end

      {
        time_seconds: minute * 60,
        time_formatted: "#{minute}:00",
        type: "CS_STATE",
        your_cs: my_cs,
        enemy_cs: enemy_cs,
        cs_diff: enemy_cs ? my_cs - enemy_cs : nil,
        your_gold: my_frame["totalGold"],
        enemy_gold: enemy_frame&.dig("totalGold"),
        your_level: my_frame["level"],
        enemy_level: enemy_frame&.dig("level")
      }
    end
  end

  private

  def find_frame_at(timestamp_ms)
    @context.timeline_frames.min_by { |f| (f["timestamp"] - timestamp_ms).abs }
  end

  def find_enemy_mid_participant_id
    enemy = @context.participants.find do |p|
      p["teamPosition"] == "MIDDLE" && p["participantId"] != @context.my_participant_id
    end
    enemy&.dig("participantId")
  end
end
