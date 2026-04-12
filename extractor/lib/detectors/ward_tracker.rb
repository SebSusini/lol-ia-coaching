# Tracks ward events from Riot API timeline events (WARD_PLACED, WARD_KILL).
# Generates VISION_STATE summaries every 5 minutes.

class WardTracker
  SUMMARY_INTERVAL = 5 # minutes
  MID_ZONE_RANGE = 3000 # distance from mid center to count as "in mid"
  MID_CENTER = [7500, 7500]

  def initialize(context)
    @context = context
  end

  def detect
    my_team_id = @context.participants.find { |p| p["participantId"] == @context.my_participant_id }&.dig("teamId")
    return [] unless my_team_id

    ward_placed_events = @context.timeline_events.select { |e| e["type"] == "WARD_PLACED" }
    ward_kill_events = @context.timeline_events.select { |e| e["type"] == "WARD_KILL" }

    game_duration_min = (@context.game_duration_seconds / 60.0).ceil
    max_interval = [game_duration_min, 60].min # cap at 60 minutes

    snapshots = []

    SUMMARY_INTERVAL.step(max_interval, SUMMARY_INTERVAL) do |minute|
      window_start_ms = (minute - SUMMARY_INTERVAL) * 60 * 1000
      window_end_ms = minute * 60 * 1000

      # Your wards placed in this window
      your_wards_placed = ward_placed_events.count do |e|
        e["timestamp"].between?(window_start_ms, window_end_ms) &&
          e["creatorId"] == @context.my_participant_id
      end

      # Your wards killed in this window
      your_wards_killed = ward_kill_events.count do |e|
        e["timestamp"].between?(window_start_ms, window_end_ms) &&
          e["killerId"] == @context.my_participant_id
      end

      # Team wards placed
      team_wards_placed = ward_placed_events.count do |e|
        e["timestamp"].between?(window_start_ms, window_end_ms) &&
          team_for_participant(e["creatorId"]) == my_team_id
      end

      # Enemy wards placed (best proxy for "wards in mid" since Riot API
      # WARD_PLACED events lack position data)
      enemy_wards_placed = ward_placed_events.count do |e|
        e["timestamp"].between?(window_start_ms, window_end_ms) &&
          team_for_participant(e["creatorId"]) != my_team_id &&
          e["creatorId"] != 0
      end

      # Enemy wards killed by your team
      enemy_wards_killed_by_team = ward_kill_events.count do |e|
        e["timestamp"].between?(window_start_ms, window_end_ms) &&
          team_for_participant(e["killerId"]) == my_team_id
      end

      # Vision advantage: your team placed more + killed more enemy wards
      team_vision_score = team_wards_placed + enemy_wards_killed_by_team
      enemy_vision_score = enemy_wards_placed
      vision_advantage = team_vision_score > enemy_vision_score

      snapshots << {
        time_seconds: minute * 60,
        time_formatted: "#{minute}:00",
        type: "VISION_STATE",
        your_wards_placed: your_wards_placed,
        your_wards_killed: your_wards_killed,
        team_wards_placed: team_wards_placed,
        enemy_wards_placed: enemy_wards_placed,
        enemy_wards_killed_by_team: enemy_wards_killed_by_team,
        vision_advantage: vision_advantage
      }
    end

    snapshots
  end

  private

  def team_for_participant(participant_id)
    return nil if participant_id.nil? || participant_id == 0
    p = @context.participants.find { |p| p["participantId"] == participant_id }
    p&.dig("teamId")
  end
end
