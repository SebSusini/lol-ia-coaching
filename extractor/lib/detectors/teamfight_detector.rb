class TeamfightDetector
  KILL_CLUSTER_WINDOW = 30 # seconds — kills within this window = same fight
  MIN_KILLS_FOR_TEAMFIGHT = 3 # at least 3 kills to count as teamfight

  def initialize(context, filtered_data)
    @context = context
    @filtered_data = filtered_data
  end

  def detect
    kill_events = @context.timeline_events.select { |e| e["type"] == "CHAMPION_KILL" }
    return [] if kill_events.empty?

    # Cluster kills by time proximity
    clusters = []
    current_cluster = [kill_events.first]

    kill_events[1..].each do |event|
      last_time = current_cluster.last["timestamp"]
      if (event["timestamp"] - last_time) <= KILL_CLUSTER_WINDOW * 1000
        current_cluster << event
      else
        clusters << current_cluster if current_cluster.size >= MIN_KILLS_FOR_TEAMFIGHT
        current_cluster = [event]
      end
    end
    clusters << current_cluster if current_cluster.size >= MIN_KILLS_FOR_TEAMFIGHT

    clusters.map do |cluster|
      start_time = cluster.first["timestamp"] / 1000.0
      end_time = cluster.last["timestamp"] / 1000.0

      your_team_kills = cluster.count { |e| your_team_kill?(e) }
      enemy_team_kills = cluster.count { |e| !your_team_kill?(e) }
      you_died = cluster.any? { |e| e["victimId"] == @context.my_participant_id }
      you_got_kill = cluster.any? { |e| e["killerId"] == @context.my_participant_id }
      you_assisted = cluster.any? { |e| (e["assistingParticipantIds"] || []).include?(@context.my_participant_id) }

      result = if your_team_kills > enemy_team_kills
        "WON"
      elsif your_team_kills < enemy_team_kills
        "LOST"
      else
        "EVEN"
      end

      {
        time_seconds: start_time,
        time_formatted: format_time(start_time),
        type: "TEAMFIGHT",
        duration_seconds: (end_time - start_time).round(1),
        total_kills: cluster.size,
        your_team_kills: your_team_kills,
        enemy_team_kills: enemy_team_kills,
        result: result,
        you_died: you_died,
        you_got_kill: you_got_kill,
        you_participated: you_got_kill || you_assisted,
        kills_detail: cluster.map { |e|
          killer = @context.participants.find { |p| p["participantId"] == e["killerId"] }
          victim = @context.participants.find { |p| p["participantId"] == e["victimId"] }
          { killer: killer&.dig("championName"), victim: victim&.dig("championName") }
        }
      }
    end
  end

  private

  def your_team_kill?(event)
    killer = @context.participants.find { |p| p["participantId"] == event["killerId"] }
    return false unless killer
    killer["teamId"] == @context.participants.find { |p| p["participantId"] == @context.my_participant_id }&.dig("teamId")
  end

  def format_time(seconds)
    "%d:%02d" % [seconds / 60, seconds % 60]
  end
end
