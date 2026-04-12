class ObjectiveDetector
  def initialize(context, filtered_data)
    @context = context
    @filtered_data = filtered_data
  end

  def detect
    objectives = @context.timeline_events.select { |e| e["type"] == "ELITE_MONSTER_KILL" }
    my_team_id = @context.participants.find { |p| p["participantId"] == @context.my_participant_id }&.dig("teamId")

    objectives.map do |event|
      ts = event["timestamp"] / 1000.0
      taken_by_your_team = event["killerTeamId"] == my_team_id

      {
        time_seconds: ts,
        time_formatted: format_time(ts),
        type: "OBJECTIVE",
        monster: format_monster(event["monsterType"]),
        sub_type: format_sub_type(event["monsterSubType"]),
        taken_by: taken_by_your_team ? "YOUR_TEAM" : "ENEMY_TEAM"
      }
    end
  end

  private

  def format_monster(type)
    case type
    when "DRAGON" then "Dragon"
    when "BARON_NASHOR" then "Baron"
    when "RIFTHERALD" then "Herald"
    when "HORDE" then "Voidgrubs"
    else type
    end
  end

  def format_sub_type(sub)
    return nil unless sub
    case sub
    when "FIRE_DRAGON" then "Infernal"
    when "EARTH_DRAGON" then "Mountain"
    when "WATER_DRAGON" then "Ocean"
    when "AIR_DRAGON" then "Cloud"
    when "HEXTECH_DRAGON" then "Hextech"
    when "CHEMTECH_DRAGON" then "Chemtech"
    when "ELDER_DRAGON" then "Elder"
    else sub
    end
  end

  def format_time(seconds)
    "%d:%02d" % [seconds / 60, seconds % 60]
  end
end
