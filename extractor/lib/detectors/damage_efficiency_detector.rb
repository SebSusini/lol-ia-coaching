# End-of-game analysis of damage efficiency (damage/gold) compared to
# lane opponent and team averages.

class DamageEfficiencyDetector
  def initialize(context)
    @context = context
  end

  def detect
    my_participant = find_my_participant
    enemy_laner = find_enemy_laner
    return [] unless my_participant

    my_team_id = my_participant["teamId"]
    teammates = @context.participants.select { |p| p["teamId"] == my_team_id }

    my_damage = my_participant["totalDamageDealtToChampions"] || 0
    my_gold = my_participant["goldEarned"] || 1
    my_dpg = (my_damage.to_f / my_gold).round(2)

    # Enemy laner damage per gold
    enemy_dpg = if enemy_laner
      enemy_damage = enemy_laner["totalDamageDealtToChampions"] || 0
      enemy_gold = enemy_laner["goldEarned"] || 1
      (enemy_damage.to_f / enemy_gold).round(2)
    end

    # Team average damage per gold
    team_dpgs = teammates.map do |p|
      dmg = p["totalDamageDealtToChampions"] || 0
      gold = p["goldEarned"] || 1
      dmg.to_f / gold
    end
    team_avg_dpg = team_dpgs.empty? ? 0 : (team_dpgs.sum / team_dpgs.size).round(2)

    # Damage share (% of team's total damage)
    team_total_damage = teammates.sum { |p| p["totalDamageDealtToChampions"] || 0 }
    damage_share = team_total_damage > 0 ? ((my_damage.to_f / team_total_damage) * 100).round(0) : 0

    verdict = assess_verdict(my_dpg, enemy_dpg, team_avg_dpg)

    [{
      time_seconds: 0,
      time_formatted: "END",
      type: "DAMAGE_EFFICIENCY",
      your_damage: my_damage,
      your_gold: my_gold,
      your_damage_per_gold: my_dpg,
      enemy_laner_damage_per_gold: enemy_dpg,
      team_avg_damage_per_gold: team_avg_dpg,
      your_damage_share: damage_share,
      verdict: verdict
    }]
  end

  private

  def find_my_participant
    @context.participants.find { |p| p["participantId"] == @context.my_participant_id }
  end

  def find_enemy_laner
    my = find_my_participant
    return nil unless my
    @context.participants.find do |p|
      p["teamPosition"] == my["teamPosition"] && p["teamId"] != my["teamId"]
    end
  end

  def assess_verdict(my_dpg, enemy_dpg, team_avg)
    # Compare against both enemy laner and team average
    if enemy_dpg
      vs_enemy = my_dpg / enemy_dpg if enemy_dpg > 0
      vs_team = my_dpg / team_avg if team_avg > 0

      if vs_enemy && vs_enemy >= 1.2 && vs_team && vs_team >= 1.1
        "EXCELLENT"
      elsif vs_enemy && vs_enemy >= 1.0 && vs_team && vs_team >= 1.0
        "GOOD"
      elsif vs_enemy && vs_enemy >= 0.85 || (vs_team && vs_team >= 0.95)
        "AVERAGE"
      else
        "BELOW_AVERAGE"
      end
    else
      # No enemy laner data, compare only to team
      vs_team = team_avg > 0 ? my_dpg / team_avg : 1.0
      if vs_team >= 1.2
        "EXCELLENT"
      elsif vs_team >= 1.0
        "GOOD"
      elsif vs_team >= 0.85
        "AVERAGE"
      else
        "BELOW_AVERAGE"
      end
    end
  end
end
