require "json"

class ReviewFormatter
  def initialize(context, timeline_events)
    @context = context
    @timeline_events = timeline_events
  end

  def format
    {
      meta: build_meta,
      final_stats: build_final_stats,
      timeline: @timeline_events,
      patterns: build_patterns
    }
  end

  def to_json
    JSON.pretty_generate(format)
  end

  private

  def build_meta
    {
      champion: @context.my_champion,
      enemy_mid: { champion: @context.enemy_mid_champion },
      result: @context.result,
      elo: "Emerald 2",
      game_version: @context.game_version,
      duration_seconds: @context.game_duration_seconds,
      team: @context.my_team
    }
  end

  def build_final_stats
    my_participant = @context.participants.find { |p| p["participantId"] == @context.my_participant_id }
    return {} unless my_participant

    {
      kda: "#{my_participant['kills']}/#{my_participant['deaths']}/#{my_participant['assists']}",
      cs: my_participant["totalMinionsKilled"] + my_participant["neutralMinionsKilled"],
      cs_per_min: ((my_participant["totalMinionsKilled"] + my_participant["neutralMinionsKilled"]).to_f / (@context.game_duration_seconds / 60.0)).round(1),
      gold: my_participant["goldEarned"],
      damage_to_champions: my_participant["totalDamageDealtToChampions"],
      damage_taken: my_participant["totalDamageTaken"],
      vision_score: my_participant["visionScore"],
      items_final: (0..6).map { |i| my_participant["item#{i}"] }.reject { |i| i == 0 }
    }
  end

  def build_patterns
    deaths = @timeline_events.select { |e| e[:type] == "DEATH" }
    roams = @timeline_events.select { |e| e[:type] == "ROAM" }
    cs_states = @timeline_events.select { |e| e[:type] == "CS_STATE" }

    {
      death_count: deaths.size,
      death_timings: deaths.map { |d| d[:time_formatted] },
      gold_unspent_at_deaths: deaths.map { |d| d[:gold_unspent] }.compact,
      roams_attempted: roams.size,
      roams_successful: roams.count { |r| r[:result] == "KILL" },
      cs_at_5: cs_states.find { |c| c[:time_formatted] == "5:00" }&.dig(:your_cs),
      cs_at_10: cs_states.find { |c| c[:time_formatted] == "10:00" }&.dig(:your_cs),
      cs_at_15: cs_states.find { |c| c[:time_formatted] == "15:00" }&.dig(:your_cs)
    }
  end
end
