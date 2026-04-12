# Tracks enemy jungler position every minute using Riot API timeline frames.
# Detects when the enemy jungler is near the player and assesses danger level.

class JunglerTracker
  NEAR_DISTANCE = 3000 # units — jungler is considered "near you"
  MID_CENTER = [7500, 7500]

  def initialize(context)
    @context = context
    @tracker = PositionTracker.new(context)
  end

  def detect
    enemy_jungler = find_enemy_jungler
    return [] unless enemy_jungler

    enemy_jungler_id = enemy_jungler["participantId"]
    enemy_jungler_name = enemy_jungler["championName"]

    @context.timeline_frames.filter_map do |frame|
      ts_ms = frame["timestamp"]
      ts_sec = ts_ms / 1000.0
      next if ts_sec < 90 # Skip very early game (before jungle clear)

      jungler_pf = frame.dig("participantFrames", enemy_jungler_id.to_s)
      my_pf = frame.dig("participantFrames", @context.my_participant_id.to_s)
      next unless jungler_pf&.dig("position") && my_pf&.dig("position")

      jx = jungler_pf["position"]["x"]
      jy = jungler_pf["position"]["y"]
      mx = my_pf["position"]["x"]
      my = my_pf["position"]["y"]

      zone = classify_zone(jx, jy)
      dist = distance(jx, jy, mx, my)
      near_you = dist < NEAR_DISTANCE
      pushed_up = player_pushed_up?(mx, my)
      danger = assess_danger(near_you, pushed_up, zone)

      {
        time_seconds: ts_sec,
        time_formatted: format_time(ts_sec),
        type: "JUNGLER_POSITION",
        enemy_jungler: enemy_jungler_name,
        zone: zone,
        near_you: near_you,
        distance: dist.round(0),
        danger_level: danger
      }
    end
  end

  private

  def find_enemy_jungler
    my_team_id = @context.participants.find { |p| p["participantId"] == @context.my_participant_id }&.dig("teamId")
    @context.participants.find do |p|
      p["teamPosition"] == "JUNGLE" && p["teamId"] != my_team_id
    end
  end

  def classify_zone(x, y)
    return "BASE" if near?(x, y, [400, 400], 3000) || near?(x, y, [14300, 14300], 3000)
    return "DRAGON" if near?(x, y, [9866, 4414], 2500)
    return "BARON" if near?(x, y, [4966, 10542], 2500)
    return "MID" if in_mid_lane?(x, y)
    return "TOP" if in_top_lane?(x, y)
    return "BOT" if in_bot_lane?(x, y)
    "JUNGLE"
  end

  def near?(x, y, target, radius)
    distance(x, y, target[0], target[1]) < radius
  end

  def in_mid_lane?(x, y)
    (x - y).abs < 3000 && x.between?(3000, 12000) && y.between?(3000, 12000)
  end

  def in_top_lane?(x, y)
    (x < 3500 && y > 5000) || (y > 12000 && x < 10000)
  end

  def in_bot_lane?(x, y)
    (x > 10000 && y < 5000) || (y < 3500 && x > 5000)
  end

  def distance(x1, y1, x2, y2)
    Math.sqrt((x1 - x2)**2 + (y1 - y2)**2)
  end

  # Player is "pushed up" if they are past the mid point of the lane
  # toward the enemy side. For Blue team, that means high x/y; for Red, low x/y.
  def player_pushed_up?(x, y)
    if @context.my_team == "Blue"
      x > 8500 && y > 8500
    else
      x < 6500 && y < 6500
    end
  end

  def assess_danger(near_you, pushed_up, zone)
    if near_you && pushed_up
      "HIGH"
    elsif near_you
      "MEDIUM"
    elsif zone == "MID"
      "LOW"
    else
      "NONE"
    end
  end

  def format_time(seconds)
    minutes = (seconds / 60).to_i
    secs = (seconds % 60).to_i
    "%d:%02d" % [minutes, secs]
  end
end
