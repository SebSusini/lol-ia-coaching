# Tracks player positions from Riot API timeline frames (1 per minute)
# Detects roams, side lane farming, objective presence

class PositionTracker
  # Summoner's Rift zones (approximate)
  ZONES = {
    mid_lane: { x: [4500, 10500], y: [4500, 10500], diagonal: true },
    top_lane: { x: [0, 4500], y: [4500, 15000] },
    bot_lane: { x: [4500, 15000], y: [0, 4500] },
    dragon_pit: { x: [9000, 11000], y: [3000, 5000] },
    baron_pit: { x: [3500, 5500], y: [9500, 11500] },
    blue_base: { x: [0, 4000], y: [0, 4000] },
    red_base: { x: [11000, 15000], y: [11000, 15000] }
  }.freeze

  def initialize(context, filtered_data)
    @context = context
    @filtered_data = filtered_data
  end

  def track
    positions = []

    @context.timeline_frames.each do |frame|
      ts_min = frame["timestamp"] / 60000.0
      next unless ts_min >= 1

      my_frame = frame.dig("participantFrames", @context.my_participant_id.to_s)
      next unless my_frame && my_frame["position"]

      x = my_frame["position"]["x"]
      y = my_frame["position"]["y"]
      zone = classify_zone(x, y)

      positions << {
        time_min: ts_min.round(1),
        x: x,
        y: y,
        zone: zone
      }
    end

    positions
  end

  # Returns a summary of where the player spent time
  def zone_summary
    positions = track
    return {} if positions.empty?

    zone_counts = positions.group_by { |p| p[:zone] }.transform_values(&:size)
    total = positions.size.to_f

    zone_counts.transform_values { |count| (count / total * 100).round(0) }
  end

  private

  def classify_zone(x, y)
    return "BASE" if in_zone?(x, y, ZONES[:blue_base]) || in_zone?(x, y, ZONES[:red_base])
    return "DRAGON" if in_zone?(x, y, ZONES[:dragon_pit])
    return "BARON" if in_zone?(x, y, ZONES[:baron_pit])

    if in_mid_lane?(x, y)
      "MID"
    elsif in_zone?(x, y, ZONES[:top_lane])
      "TOP"
    elsif in_zone?(x, y, ZONES[:bot_lane])
      "BOT"
    else
      "JUNGLE"
    end
  end

  def in_zone?(x, y, zone)
    x.between?(zone[:x][0], zone[:x][1]) && y.between?(zone[:y][0], zone[:y][1])
  end

  def in_mid_lane?(x, y)
    # Mid lane runs diagonally — check distance from diagonal
    (x - y).abs < 3000 && x.between?(3000, 12000) && y.between?(3000, 12000)
  end
end
