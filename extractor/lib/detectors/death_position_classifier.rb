# Classifies death positions to determine context (dive, gank, overextend, etc.)
# Uses Summoner's Rift turret positions to determine if a death was under tower

class DeathPositionClassifier
  # Approximate turret positions on Summoner's Rift
  TURRETS = {
    blue: {
      mid_t1: [5048, 4812],
      mid_t2: [3651, 3696],
      top_t1: [981, 10441],
      bot_t1: [10504, 1029]
    },
    red: {
      mid_t1: [9767, 10113],
      mid_t2: [11134, 11207],
      top_t1: [4318, 13875],
      bot_t1: [13866, 4505]
    }
  }.freeze

  TOWER_RANGE = 1200
  MID_LANE_CENTER = [7400, 7400]

  def initialize(team) # "Blue" or "Red"
    @team = team.downcase.to_sym
    @my_turrets = TURRETS[@team]
    @enemy_turrets = TURRETS[@team == :blue ? :red : :blue]
  end

  def classify(position)
    return { zone: "UNKNOWN", detail: "No position data" } unless position

    x = position["x"] || position[:x]
    y = position["y"] || position[:y]
    return { zone: "UNKNOWN", detail: "No position data" } unless x && y

    # Check if near any of your turrets (= you got dove)
    if near_turret?(x, y, @my_turrets)
      return { zone: "DOVE_UNDER_YOUR_TOWER", detail: "Tu te fais dive sous ta tour", evitable: "Difficilement" }
    end

    # Check if near enemy turrets (= you overextended)
    if near_turret?(x, y, @enemy_turrets)
      return { zone: "NEAR_ENEMY_TOWER", detail: "Tu es trop avance pres de la tour adverse", evitable: "Oui" }
    end

    # Check map zones
    if in_river?(x, y)
      return { zone: "RIVER", detail: "Catch en riviere / rotation", evitable: "Vision" }
    end

    if in_jungle?(x, y)
      return { zone: "JUNGLE", detail: "Catch en jungle", evitable: "Pathing / vision" }
    end

    if in_mid_lane?(x, y)
      side = my_side?(x, y) ? "YOUR_SIDE" : "ENEMY_SIDE"
      detail = my_side?(x, y) ? "Ton cote de la lane" : "Cote ennemi de la lane"
      return { zone: "MID_#{side}", detail: detail, evitable: my_side?(x, y) ? "Partiellement" : "Oui" }
    end

    { zone: "OTHER", detail: "Position non classifiee (x:#{x.round(0)}, y:#{y.round(0)})", evitable: "?" }
  end

  private

  def near_turret?(x, y, turrets)
    turrets.values.any? { |pos| distance(x, y, pos[0], pos[1]) < TOWER_RANGE }
  end

  def distance(x1, y1, x2, y2)
    Math.sqrt((x1 - x2)**2 + (y1 - y2)**2)
  end

  def in_river?(x, y)
    # River runs diagonally — approximate with two zones
    (x.between?(1500, 6000) && y.between?(9000, 13000)) ||
      (x.between?(8500, 13500) && y.between?(1500, 5500)) ||
      (x.between?(3500, 7000) && y.between?(6500, 9500)) ||
      (x.between?(7000, 11000) && y.between?(5000, 8000))
  end

  def in_jungle?(x, y)
    !in_lane?(x, y) && !in_river?(x, y) && x.between?(500, 14000) && y.between?(500, 14000)
  end

  def in_mid_lane?(x, y)
    dist_to_mid = distance(x, y, MID_LANE_CENTER[0], MID_LANE_CENTER[1])
    # Mid lane corridor: within ~3000 units of the diagonal
    diff = (x - y).abs
    diff < 3000 && x.between?(3000, 12000) && y.between?(3000, 12000)
  end

  def in_lane?(x, y)
    in_mid_lane?(x, y) || in_top_lane?(x, y) || in_bot_lane?(x, y)
  end

  def in_top_lane?(x, y)
    (x < 3000 && y > 3000) || (y > 12000 && x < 12000)
  end

  def in_bot_lane?(x, y)
    (x > 12000 && y < 12000) || (y < 3000 && x > 3000)
  end

  def my_side?(x, y)
    if @team == :blue
      x < 7400 && y < 7400
    else
      x > 7400 && y > 7400
    end
  end
end
