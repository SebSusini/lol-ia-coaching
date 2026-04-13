require "json"
require "date"

class ProgressionTracker
  PROGRESSION_FILE = File.join(__dir__, "..", "..", "output", "progression.json")

  attr_reader :entries

  def initialize(db_path: PROGRESSION_FILE)
    @db_path = db_path
    @entries = load_db
  end

  # Record a new review into the progression database.
  # +review+ is the hash returned by Extractor#extract (with symbolised keys).
  def record(review)
    entry = extract_entry(review)
    return nil unless entry

    # Avoid duplicates
    unless @entries.any? { |e| e["match_id"] == entry["match_id"] }
      @entries << entry
      save_db
    end

    entry
  end

  # ---------- Analysis methods ----------

  # Compute the trend for a numeric stat over the last N games.
  # Returns { direction: "improving"|"declining"|"stable", last_n_avg: Float, prev_n_avg: Float, change_pct: Float }
  def trend(stat, last_n = 5)
    values = @entries.last(last_n * 2).map { |e| e[stat.to_s] }.compact
    return nil if values.size < 2

    recent = values.last([last_n, values.size].min)
    prev_count = [values.size - recent.size, 1].max
    previous = values.first(prev_count)

    recent_avg = (recent.sum.to_f / recent.size).round(2)
    prev_avg = (previous.sum.to_f / previous.size).round(2)

    change_pct = prev_avg != 0 ? (((recent_avg - prev_avg) / prev_avg.abs) * 100).round(0) : 0

    direction = if change_pct.abs < 10
      "stable"
    elsif lower_is_better?(stat)
      change_pct < 0 ? "improving" : "declining"
    else
      change_pct > 0 ? "improving" : "declining"
    end

    { direction: direction, last_n_avg: recent_avg, prev_n_avg: prev_avg, change_pct: change_pct }
  end

  # Rolling average for a stat over the last N games.
  def average(stat, last_n = 5)
    values = @entries.last(last_n).map { |e| e[stat.to_s] }.compact
    return nil if values.empty?
    (values.sum.to_f / values.size).round(2)
  end

  # Compare current review stats to historical averages.
  # Returns array of comparison strings.
  def compare_to_previous(current_review)
    entry = extract_entry(current_review)
    return [] unless entry
    return [] if @entries.size < 2

    comparisons = []
    tracked_stats.each do |stat, label|
      current_val = entry[stat]
      next unless current_val

      avg = average(stat, 5)
      next unless avg

      diff = current_val - avg
      next if diff.abs < 0.1

      direction = if lower_is_better?(stat)
        diff < 0 ? "improved" : "worse"
      else
        diff > 0 ? "improved" : "worse"
      end

      comparisons << "#{label}: #{current_val} (avg #{avg}, #{direction})"
    end

    comparisons
  end

  # Persistent weaknesses: stats that are consistently bad.
  def weaknesses
    return [] if @entries.size < 3

    issues = []

    # Vision score rating
    poor_vision = @entries.last(15).count { |e| vision_rating(e) == "poor" }
    total = [@entries.size, 15].min
    if poor_vision > total * 0.6
      issues << "vision_score_rating: poor (#{poor_vision}/#{total} games)"
    end

    # Deaths in river
    river_deaths = @entries.last(15).sum { |e| (e["deaths_by_zone"] || {}).fetch("RIVER", 0) }
    total_deaths = @entries.last(15).sum { |e| e["deaths"] || 0 }
    if total_deaths > 0 && river_deaths.to_f / total_deaths > 0.4
      pct = ((river_deaths.to_f / total_deaths) * 100).round(0)
      issues << "deaths_in_river: #{pct}% of deaths"
    end

    # High death count
    avg_deaths = average("deaths", 10)
    if avg_deaths && avg_deaths > 5.5
      issues << "high_deaths: #{avg_deaths} avg deaths per game"
    end

    # Low CS
    avg_cs = average("cs_per_min", 10)
    if avg_cs && avg_cs < 6.0
      issues << "low_cs: #{avg_cs} CS/min avg"
    end

    # Low kill participation
    avg_kp = average("kill_participation", 10)
    if avg_kp && avg_kp < 45
      issues << "low_kill_participation: #{avg_kp.round(0)}% avg"
    end

    # Low damage share
    avg_dmg = average("damage_share", 10)
    if avg_dmg && avg_dmg < 18
      issues << "low_damage_share: #{avg_dmg.round(0)}% avg"
    end

    issues
  end

  # Consistently good stats.
  def strengths
    return [] if @entries.size < 3

    goods = []

    avg_cs = average("cs_per_min", 10)
    goods << "strong_cs: #{avg_cs} CS/min avg" if avg_cs && avg_cs >= 7.5

    avg_kp = average("kill_participation", 10)
    goods << "high_kill_participation: #{avg_kp.round(0)}% avg" if avg_kp && avg_kp >= 60

    avg_deaths = average("deaths", 10)
    goods << "low_deaths: #{avg_deaths} avg deaths" if avg_deaths && avg_deaths <= 3.5

    avg_dmg = average("damage_share", 10)
    goods << "high_damage_share: #{avg_dmg.round(0)}% avg" if avg_dmg && avg_dmg >= 25

    good_vision = @entries.last(10).count { |e| vision_rating(e) == "good" }
    goods << "strong_vision: good in #{good_vision}/#{[@entries.size, 10].min} games" if good_vision >= 6

    goods
  end

  # Build the progression summary for inclusion in review JSON.
  def progression_summary
    return nil if @entries.empty?

    stats_to_track = %w[cs_per_min vision_score deaths kill_participation damage_share]
    trends = {}
    stats_to_track.each do |stat|
      t = trend(stat, 5)
      next unless t
      trends[stat] = t
    end

    recent_improvements = []
    stats_to_track.each do |stat|
      t = trends[stat]
      next unless t && t[:direction] == "improving"
      recent_improvements << "#{stat_label(stat)} #{lower_is_better?(stat) ? 'dropped' : 'rose'} from #{t[:prev_n_avg]} to #{t[:last_n_avg]} avg (#{t[:change_pct].abs}%)"
    end

    {
      games_tracked: @entries.size,
      trends: trends,
      persistent_weaknesses: weaknesses,
      persistent_strengths: strengths,
      recent_improvement: recent_improvements.first
    }
  end

  # Format a human-readable progression report.
  def format_report
    return "No games tracked yet." if @entries.empty?

    summoner = @entries.last["summoner"] || "Player"
    lines = []
    lines << "=== Progression -- #{summoner} (#{@entries.size} games tracked) ==="
    lines << ""

    # Win rate
    wins = @entries.count { |e| e["result"] == "WIN" }
    lines << "Win rate: #{wins}/#{@entries.size} (#{(wins.to_f / @entries.size * 100).round(0)}%)"
    lines << ""

    # Stats table
    stats = %w[cs_per_min vision_score deaths kill_participation damage_share]
    header = "%-12s %8s %8s %20s" % ["", "Last 5", "Prev 5", "Trend"]
    lines << header

    stats.each do |stat|
      t = trend(stat, 5)
      next unless t

      label = stat_label(stat)
      last_val = format_stat_value(stat, t[:last_n_avg])
      prev_val = format_stat_value(stat, t[:prev_n_avg])

      arrow = case t[:direction]
        when "improving" then "^ improving"
        when "declining" then "v declining"
        else "= stable"
      end

      change = t[:change_pct] != 0 ? " (#{t[:change_pct] > 0 ? '+' : ''}#{t[:change_pct]}%)" : ""
      lines << "%-12s %8s %8s %20s" % [label + ":", last_val, prev_val, arrow + change]
    end

    # Weaknesses
    w = weaknesses
    unless w.empty?
      lines << ""
      lines << "Persistent weaknesses:"
      w.each { |issue| lines << "  - #{issue}" }
    end

    # Strengths
    s = strengths
    unless s.empty?
      lines << ""
      lines << "Strengths:"
      s.each { |good| lines << "  - #{good}" }
    end

    # Recent improvements
    improvements = stats.filter_map do |stat|
      t = trend(stat, 5)
      next unless t && t[:direction] == "improving"
      "  - #{stat_label(stat)} #{lower_is_better?(stat) ? 'down' : 'up'} #{t[:change_pct].abs}%"
    end
    unless improvements.empty?
      lines << ""
      lines << "Recent wins:"
      lines.concat(improvements)
    end

    # Last 5 games
    lines << ""
    lines << "Last 5 games:"
    @entries.last(5).reverse.each do |e|
      result_marker = e["result"] == "WIN" ? "W" : "L"
      lines << "  #{e['date']} | #{e['champion']} #{e['role']} | #{e['kda']} | #{result_marker} | #{e['cs_per_min']} CS/m | Vision #{e['vision_score']}"
    end

    lines.join("\n")
  end

  private

  def load_db
    return [] unless File.exist?(@db_path)
    data = JSON.parse(File.read(@db_path))
    data.is_a?(Array) ? data : []
  rescue JSON::ParserError
    []
  end

  def save_db
    File.write(@db_path, JSON.pretty_generate(@entries))
  end

  # Extract a flat entry hash from a review hash (symbol or string keys).
  def extract_entry(review)
    # Support both symbol and string keys
    meta = review[:meta] || review["meta"]
    stats = review[:final_stats] || review["final_stats"]
    patterns = review[:patterns] || review["patterns"]
    gold_curve = review[:gold_curve] || review["gold_curve"]

    return nil unless meta && stats

    champion = meta[:champion] || meta["champion"]
    role = meta[:role] || meta["role"]
    result = meta[:result] || meta["result"]
    duration = meta[:duration_seconds] || meta["duration_seconds"]
    kda_str = stats[:kda] || stats["kda"]

    kills, deaths, assists = (kda_str || "0/0/0").split("/").map(&:to_i)

    # Team total damage for damage share calculation
    damage = stats[:damage_to_champions] || stats["damage_to_champions"] || 0
    team_participants = (meta[:teammates] || meta["teammates"] || [])
    # Damage share is pre-calculated in patterns or final_stats
    damage_share = nil
    if patterns
      dmg_event = nil
      timeline = review[:timeline] || review["timeline"] || []
      timeline.each do |event|
        ev_type = event[:type] || event["type"]
        if ev_type == "DAMAGE_EFFICIENCY"
          dmg_event = event
          break
        end
      end
      damage_share = (dmg_event[:your_damage_share] || dmg_event["your_damage_share"]) if dmg_event
    end

    # Gold diff at 15
    gold_diff_at_15 = nil
    (gold_curve || []).each do |point|
      t = point[:time_min] || point["time_min"]
      if t == 15
        gold_diff_at_15 = point[:gold_diff] || point["gold_diff"]
        break
      end
    end

    # Deaths by zone
    deaths_by_zone = patterns ? (patterns[:deaths_by_zone] || patterns["deaths_by_zone"]) : nil

    # Match ID from timeline data if available
    match_id = meta[:match_id] || meta["match_id"]

    # Summoner from environment or meta
    summoner = ENV["RIOT_SUMMONER_NAME"] || meta[:summoner] || meta["summoner"] || ""

    {
      "date" => Date.today.to_s,
      "match_id" => match_id,
      "summoner" => summoner,
      "champion" => champion,
      "role" => role,
      "result" => result,
      "duration_seconds" => duration,
      "kda" => kda_str,
      "kills" => kills,
      "deaths" => deaths,
      "assists" => assists,
      "cs" => stats[:cs] || stats["cs"],
      "cs_per_min" => stats[:cs_per_min] || stats["cs_per_min"],
      "vision_score" => stats[:vision_score] || stats["vision_score"],
      "kill_participation" => stats[:kill_participation] || stats["kill_participation"],
      "damage_to_champions" => damage,
      "damage_share" => damage_share,
      "gold" => stats[:gold] || stats["gold"],
      "gold_diff_at_15" => gold_diff_at_15,
      "deaths_by_zone" => deaths_by_zone,
      "vision_score_rating" => patterns ? (patterns[:vision_score_rating] || patterns["vision_score_rating"]) : nil,
      "damage_efficiency" => patterns ? (patterns[:damage_efficiency_verdict] || patterns["damage_efficiency_verdict"]) : nil
    }
  end

  def tracked_stats
    {
      "cs_per_min" => "CS/min",
      "vision_score" => "Vision",
      "deaths" => "Deaths",
      "kill_participation" => "KP",
      "damage_share" => "DMG%"
    }
  end

  def stat_label(stat)
    {
      "cs_per_min" => "CS/min",
      "vision_score" => "Vision",
      "deaths" => "Deaths",
      "kill_participation" => "KP",
      "damage_share" => "DMG%",
      "kills" => "Kills",
      "assists" => "Assists",
      "gold" => "Gold"
    }[stat] || stat
  end

  def format_stat_value(stat, value)
    case stat
    when "kill_participation", "damage_share"
      "#{value.round(0)}%"
    else
      value.to_s
    end
  end

  # Stats where lower is better.
  def lower_is_better?(stat)
    %w[deaths].include?(stat.to_s)
  end

  def vision_rating(entry)
    entry["vision_score_rating"] || begin
      vs = entry["vision_score"] || 0
      dur = entry["duration_seconds"] || 1800
      vs_per_min = vs.to_f / (dur / 60.0)
      if vs_per_min >= 1.5 then "good"
      elsif vs_per_min >= 0.8 then "average"
      else "poor"
      end
    end
  end
end
