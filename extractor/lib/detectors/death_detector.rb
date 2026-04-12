class DeathDetector
  def initialize(context, filtered_data)
    @context = context
    @filtered_data = filtered_data
  end

  def detect
    deaths = @filtered_data[:kill_events].select do |event|
      event["victimId"] == @context.my_participant_id
    end

    deaths.map do |event|
      timestamp = event["timestamp"] / 1000.0 # Convert ms to seconds

      killer_id = event["killerId"]
      killer = @context.participants.find { |p| p["participantId"] == killer_id }
      assisters = (event["assistingParticipantIds"] || []).map do |aid|
        @context.participants.find { |p| p["participantId"] == aid }&.dig("championName")
      end.compact

      position = @context.player_position_at(@context.my_champion, timestamp)

      # Find gold state from nearest timeline frame
      frame = nearest_frame(timestamp)
      gold_unspent = frame_participant_gold(frame)

      {
        time_seconds: timestamp,
        time_formatted: format_time(timestamp),
        type: "DEATH",
        killed_by: killer&.dig("championName") || "Unknown",
        assisted_by: assisters,
        position: position,
        gold_unspent: gold_unspent,
        ward_coverage_mid: mid_wards_active_at(timestamp)
      }
    end
  end

  private

  def nearest_frame(timestamp_seconds)
    timestamp_ms = (timestamp_seconds * 1000).to_i
    @context.timeline_frames.min_by { |f| (f["timestamp"] - timestamp_ms).abs }
  end

  def frame_participant_gold(frame)
    return nil unless frame

    participant_frame = frame.dig("participantFrames", @context.my_participant_id.to_s)
    return nil unless participant_frame

    participant_frame["currentGold"]
  end

  def mid_wards_active_at(timestamp)
    return 0 unless @filtered_data[:ward_events]

    @filtered_data[:ward_events].count do |ward|
      ward["timestamp"] <= timestamp &&
        (ward["timestamp"] + ward["duration"]) >= timestamp
    end
  end

  def format_time(seconds)
    minutes = (seconds / 60).to_i
    secs = (seconds % 60).to_i
    "%d:%02d" % [minutes, secs]
  end
end
