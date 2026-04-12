class MidLaneFilter
  # Mid lane approximate boundaries on Summoner's Rift
  # The map is 14870x14980 units
  MID_LANE_X_MIN = 4000
  MID_LANE_X_MAX = 11000
  MID_LANE_Y_MIN = 4000
  MID_LANE_Y_MAX = 11000

  def initialize(context)
    @context = context
  end

  def filter
    {
      kill_events: filter_kill_events,
      positions: filter_positions,
      ward_events: filter_ward_events
    }
  end

  private

  def filter_kill_events
    @context.timeline_events.select { |e| e["type"] == "CHAMPION_KILL" }
  end

  def filter_positions
    return [] unless @context.positions_data["players_state"]

    @context.positions_data["players_state"].map do |state|
      mid_players = state["players"]&.select do |p|
        p["role"] == "Mid" || p["champ"] == @context.my_champion || p["champ"] == @context.enemy_mid_champion
      end

      {
        "timestamp" => state["timestamp"],
        "players" => mid_players || []
      }
    end
  end

  def filter_ward_events
    return [] unless @context.positions_data["wards"]

    @context.positions_data["wards"].select do |ward|
      x, y = ward["pos"]
      in_mid_zone?(x, y)
    end
  end

  def in_mid_zone?(x, y)
    x.between?(MID_LANE_X_MIN, MID_LANE_X_MAX) &&
      y.between?(MID_LANE_Y_MIN, MID_LANE_Y_MAX)
  end
end
