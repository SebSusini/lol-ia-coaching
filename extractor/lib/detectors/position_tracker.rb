# Tracks all player positions from Riot API timeline frames (1 per minute)
# Detects roams, side lane farming, objective presence, proximity to enemies

class PositionTracker
  # Summoner's Rift landmarks
  BLUE_FOUNTAIN = [400, 400]
  RED_FOUNTAIN = [14300, 14300]
  DRAGON_PIT = [9866, 4414]
  BARON_PIT = [4966, 10542]
  MID_CENTER = [7500, 7500]

  TURRETS = {
    blue_mid_t1: [5048, 4812],
    red_mid_t1: [9767, 10113],
    blue_top_t1: [981, 10441],
    red_top_t1: [4318, 13875],
    blue_bot_t1: [10504, 1029],
    red_bot_t1: [13866, 4505]
  }.freeze

  def initialize(context)
    @context = context
  end

  # Returns position snapshots for a specific player each minute
  def player_positions(participant_id)
    @context.timeline_frames.filter_map do |frame|
      ts_min = frame["timestamp"] / 60000.0
      pf = frame.dig("participantFrames", participant_id.to_s)
      next unless pf && pf["position"]

      {
        time_min: ts_min.round(1),
        x: pf["position"]["x"],
        y: pf["position"]["y"],
        zone: classify_zone(pf["position"]["x"], pf["position"]["y"]),
        gold: pf["totalGold"],
        cs: (pf["minionsKilled"] || 0) + (pf["jungleMinionsKilled"] || 0),
        level: pf["level"]
      }
    end
  end

  # Returns all 10 players' positions at a given minute
  def all_positions_at(minute)
    frame = @context.timeline_frames.min_by { |f| ((f["timestamp"] / 60000.0) - minute).abs }
    return [] unless frame

    @context.participants.filter_map do |p|
      pf = frame.dig("participantFrames", p["participantId"].to_s)
      next unless pf && pf["position"]

      {
        champion: p["championName"],
        position: p["teamPosition"],
        team: p["teamId"] == 100 ? "Blue" : "Red",
        x: pf["position"]["x"],
        y: pf["position"]["y"],
        zone: classify_zone(pf["position"]["x"], pf["position"]["y"])
      }
    end
  end

  # Detect roams from per-minute positions
  def detect_roams(participant_id, role)
    positions = player_positions(participant_id)
    return [] if positions.size < 2

    home_zone = zone_for_role(role)
    roams = []

    positions.each_cons(2) do |prev, curr|
      if prev[:zone] == home_zone && curr[:zone] != home_zone && curr[:zone] != "BASE"
        roams << {
          time_seconds: curr[:time_min] * 60,
          time_formatted: "#{curr[:time_min].to_i}:00",
          type: "ROAM",
          from: prev[:zone],
          destination: curr[:zone],
          position: { x: curr[:x], y: curr[:y] }
        }
      end
    end

    roams
  end

  # Detect if player was near an objective when it was taken
  def near_objective?(x, y, objective_type)
    target = case objective_type
    when "DRAGON" then DRAGON_PIT
    when "BARON_NASHOR" then BARON_PIT
    when "RIFTHERALD" then BARON_PIT
    else return false
    end

    distance(x, y, target[0], target[1]) < 3000
  end

  # Distance between player and enemy laner at a given minute
  def lane_proximity_at(minute, my_id, enemy_id)
    frame = @context.timeline_frames.min_by { |f| ((f["timestamp"] / 60000.0) - minute).abs }
    return nil unless frame

    my_pf = frame.dig("participantFrames", my_id.to_s)
    enemy_pf = frame.dig("participantFrames", enemy_id.to_s)
    return nil unless my_pf&.dig("position") && enemy_pf&.dig("position")

    distance(my_pf["position"]["x"], my_pf["position"]["y"],
             enemy_pf["position"]["x"], enemy_pf["position"]["y"])
  end

  private

  def classify_zone(x, y)
    return "BASE" if near?(x, y, BLUE_FOUNTAIN, 3000) || near?(x, y, RED_FOUNTAIN, 3000)
    return "DRAGON" if near?(x, y, DRAGON_PIT, 2500)
    return "BARON" if near?(x, y, BARON_PIT, 2500)
    return "MID" if in_mid_lane?(x, y)
    return "TOP" if in_top_lane?(x, y)
    return "BOT" if in_bot_lane?(x, y)
    "JUNGLE"
  end

  def near?(x, y, target, radius)
    distance(x, y, target[0], target[1]) < radius
  end

  def distance(x1, y1, x2, y2)
    Math.sqrt((x1 - x2)**2 + (y1 - y2)**2)
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

  def zone_for_role(role)
    case role
    when "MIDDLE" then "MID"
    when "TOP" then "TOP"
    when "BOTTOM" then "BOT"
    when "JUNGLE" then "JUNGLE"
    when "UTILITY" then "BOT"
    else "MID"
    end
  end
end
