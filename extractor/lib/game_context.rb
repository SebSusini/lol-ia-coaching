class GameContext
  attr_reader :positions_data, :timeline_data, :summoner_name,
              :my_champion, :enemy_mid_champion, :my_team,
              :match_info, :participants, :my_participant_id

  def initialize(positions_data, timeline_data, summoner_name)
    @positions_data = positions_data
    @timeline_data = timeline_data
    @summoner_name = summoner_name

    @match_info = timeline_data["match_info"]
    @participants = @match_info["info"]["participants"]

    resolve_player_info
  end

  def game_duration_seconds
    @match_info["info"]["gameDuration"]
  end

  def game_version
    @match_info["info"]["gameVersion"]
  end

  def result
    name_down = @summoner_name.downcase
    my_participant = @participants.find do |p|
      p["riotIdGameName"]&.downcase == name_down ||
        p["summonerName"]&.downcase == name_down
    end
    my_participant&.dig("win") ? "WIN" : "LOSS"
  end

  def timeline_frames
    @timeline_data.dig("timeline", "info", "frames") || []
  end

  def timeline_events
    timeline_frames.flat_map { |frame| frame["events"] || [] }
  end

  # Get player position at a given timestamp from Mowokuma data
  def player_position_at(champion_name, timestamp_seconds)
    return nil unless @positions_data["players_state"]

    # Find the closest snapshot to the requested timestamp
    closest = @positions_data["players_state"].min_by do |state|
      (state["timestamp"] - timestamp_seconds).abs
    end

    return nil unless closest

    player = closest["players"]&.find { |p| p["champ"] == champion_name }
    player&.dig("pos")
  end

  private

  def resolve_player_info
    name_down = @summoner_name.downcase
    my_participant = @participants.find do |p|
      p["riotIdGameName"]&.downcase == name_down ||
        p["summonerName"]&.downcase == name_down
    end

    if my_participant
      @my_champion = my_participant["championName"]
      @my_team = my_participant["teamId"] == 100 ? "Blue" : "Red"
      @my_participant_id = my_participant["participantId"]
    end

    # Find enemy mid laner
    enemy_mid = @participants.find do |p|
      p["teamPosition"] == "MIDDLE" && p["participantId"] != @my_participant_id
    end
    @enemy_mid_champion = enemy_mid&.dig("championName")
  end
end
