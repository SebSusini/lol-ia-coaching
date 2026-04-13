require "json"

# Generates a self-contained HTML file with minimap visualizations
# from a review JSON (deaths, teamfights, objectives, position trail).
class Visualizer
  MAP_SIZE = 512  # px — rendered minimap size
  GAME_MAX = 15000.0

  # Zone -> approximate center coordinates on the game map
  ZONE_COORDS = {
    "TOP"    => [2500, 12500],
    "MID"    => [7500, 7500],
    "BOT"    => [12500, 2500],
    "JUNGLE" => [5000, 9500],
    "RIVER"  => [6500, 6500],
    "DRAGON" => [9866, 4414],
    "BARON"  => [4966, 10542],
    "BASE"   => [14000, 14000],
  }.freeze

  def initialize(review_path)
    @review = JSON.parse(File.read(review_path))
    @meta = @review["meta"]
    @timeline = @review["timeline"] || []
    @position_map = @review["position_map"] || []
    @gold_curve = @review["gold_curve"] || []
    @patterns = @review["patterns"] || {}
    @final_stats = @review["final_stats"] || {}
  end

  def generate_html
    deaths = @timeline.select { |e| e["type"] == "DEATH" }
    teamfights = @timeline.select { |e| e["type"] == "TEAMFIGHT" }
    objectives = @timeline.select { |e| e["type"] == "OBJECTIVE" }

    sections = []
    sections << build_header_section
    sections << build_gold_curve_section
    sections << build_position_trail_section
    sections += deaths.each_with_index.map { |d, i| build_death_section(d, i + 1) }
    sections += teamfights.each_with_index.map { |tf, i| build_teamfight_section(tf, i + 1) }
    sections << build_objectives_section(objectives)

    wrap_html(sections.join("\n"))
  end

  def save(output_path)
    File.write(output_path, generate_html)
    output_path
  end

  private

  # --- Coordinate conversion ---

  def game_to_pixel(x, y)
    px = (x / GAME_MAX * MAP_SIZE).round(1)
    py = ((1.0 - y / GAME_MAX) * MAP_SIZE).round(1)
    [px, py]
  end

  def zone_to_pixel(zone)
    coords = ZONE_COORDS[zone]
    return nil unless coords
    game_to_pixel(coords[0], coords[1])
  end

  # --- Section builders ---

  def build_header_section
    champion = @meta["champion"] || "?"
    enemy = @meta.dig("enemy_laner", "champion") || "?"
    result = @meta["result"] || "?"
    team = @meta["team"] || "?"
    duration = @meta["duration_seconds"] ? "#{(@meta["duration_seconds"] / 60.0).round(1)} min" : "?"
    kda = @final_stats["kda"] || "?"
    cs = @final_stats["cs"] || "?"
    result_class = result == "WIN" ? "result-win" : "result-loss"

    teammates = (@meta["teammates"] || []).map { |t|
      "<span class='teammate'>#{t["champion"]} (#{t["position"]}) #{t["kda"]}</span>"
    }.join("")

    enemies = (@meta["enemies"] || []).map { |e|
      "<span class='enemy-player'>#{e["champion"]} (#{e["position"]}) #{e["kda"]}</span>"
    }.join("")

    <<~HTML
      <section class="header-section">
        <div class="game-result #{result_class}">#{result}</div>
        <h1>#{champion} vs #{enemy} — #{team} Side</h1>
        <div class="stats-row">
          <span class="stat">KDA: <strong>#{kda}</strong></span>
          <span class="stat">CS: <strong>#{cs}</strong></span>
          <span class="stat">Duration: <strong>#{duration}</strong></span>
          <span class="stat">Deaths: <strong>#{@patterns["death_count"] || "?"}</strong></span>
          <span class="stat">Vision: <strong>#{@patterns["vision_score_rating"] || "?"}</strong></span>
        </div>
        <div class="teams-row">
          <div class="team-box blue-team-box">
            <h3>#{team == "Blue" ? "Your Team" : "Enemy Team"}</h3>
            #{team == "Blue" ? teammates : enemies}
          </div>
          <div class="team-box red-team-box">
            <h3>#{team == "Red" ? "Your Team" : "Enemy Team"}</h3>
            #{team == "Red" ? teammates : enemies}
          </div>
        </div>
      </section>
    HTML
  end

  def build_gold_curve_section
    return "" if @gold_curve.empty?

    max_gold = @gold_curve.map { |g| [g["your_gold"], g["enemy_gold"]].max }.max.to_f
    max_gold = 1 if max_gold == 0
    chart_w = 500
    chart_h = 200

    your_points = @gold_curve.map.with_index { |g, i|
      x = (i.to_f / [(@gold_curve.size - 1), 1].max * chart_w).round(1)
      y = (chart_h - g["your_gold"] / max_gold * chart_h).round(1)
      "#{x},#{y}"
    }.join(" ")

    enemy_points = @gold_curve.map.with_index { |g, i|
      x = (i.to_f / [(@gold_curve.size - 1), 1].max * chart_w).round(1)
      y = (chart_h - g["enemy_gold"] / max_gold * chart_h).round(1)
      "#{x},#{y}"
    }.join(" ")

    diff_bars = @gold_curve.map.with_index { |g, i|
      x = (i.to_f / [(@gold_curve.size - 1), 1].max * chart_w).round(1)
      diff = g["gold_diff"] || 0
      color = diff >= 0 ? "#4aa3df" : "#e74c3c"
      label = "#{g["time_min"]}min: #{diff > 0 ? '+' : ''}#{diff}g"
      "<g><title>#{label}</title><rect x='#{x - 15}' y='#{chart_h + 30}' width='30' height='#{(diff.abs / max_gold * 80).round(1).clamp(2, 80)}' fill='#{color}' opacity='0.7' transform='#{diff < 0 ? '' : "scale(1,-1) translate(0,-#{2 * (chart_h + 30)})"}'/>
      <text x='#{x}' y='#{chart_h + 25}' text-anchor='middle' fill='#888' font-size='10'>#{g["time_min"]}m</text></g>"
    }.join("\n")

    <<~HTML
      <section class="event-section">
        <h2>Gold Curve</h2>
        <div class="chart-container">
          <svg width="#{chart_w + 40}" height="#{chart_h + 120}" viewBox="-20 -10 #{chart_w + 40} #{chart_h + 120}">
            <rect x="0" y="0" width="#{chart_w}" height="#{chart_h}" fill="#1a1a2e" rx="4"/>
            <polyline points="#{your_points}" fill="none" stroke="#f1c40f" stroke-width="2.5"/>
            <polyline points="#{enemy_points}" fill="none" stroke="#e74c3c" stroke-width="2.5"/>
            #{@gold_curve.map.with_index { |g, i|
              x = (i.to_f / [(@gold_curve.size - 1), 1].max * chart_w).round(1)
              yy = (chart_h - g["your_gold"] / max_gold * chart_h).round(1)
              ye = (chart_h - g["enemy_gold"] / max_gold * chart_h).round(1)
              "<circle cx='#{x}' cy='#{yy}' r='4' fill='#f1c40f'><title>You: #{g["your_gold"]}g</title></circle>" \
              "<circle cx='#{x}' cy='#{ye}' r='4' fill='#e74c3c'><title>Enemy: #{g["enemy_gold"]}g</title></circle>"
            }.join("\n")}
            #{diff_bars}
            <text x="#{chart_w + 5}" y="15" fill="#f1c40f" font-size="11">You</text>
            <text x="#{chart_w + 5}" y="30" fill="#e74c3c" font-size="11">Enemy</text>
          </svg>
        </div>
      </section>
    HTML
  end

  def build_position_trail_section
    return "" if @position_map.empty?

    dots = @position_map.map { |p|
      px, py = game_to_pixel(p["x"], p["y"])
      minute = p["time_min"]
      zone = p["zone"]
      opacity = (0.3 + 0.7 * minute / [(@position_map.last["time_min"] rescue 1), 1].max).round(2)
      radius = minute == 0 ? 6 : 5

      "<g class='trail-dot' data-minute='#{minute}'>" \
      "<circle cx='#{px}' cy='#{py}' r='#{radius}' fill='#f1c40f' opacity='#{opacity}' stroke='#fff' stroke-width='0.5'/>" \
      "<title>#{minute.to_i}:00 — #{zone}</title>" \
      "</g>"
    }.join("\n")

    # Connect dots with a line
    trail_points = @position_map.map { |p|
      px, py = game_to_pixel(p["x"], p["y"])
      "#{px},#{py}"
    }.join(" ")

    <<~HTML
      <section class="event-section">
        <h2>Position Trail (per minute)</h2>
        <p class="event-desc">Gold dot = your position each minute. Line shows your movement path through the game.</p>
        <div class="minimap-container">
          #{minimap_svg(dots + "<polyline points='#{trail_points}' fill='none' stroke='#f1c40f' stroke-width='1' opacity='0.4' stroke-dasharray='4,3'/>")}
        </div>
      </section>
    HTML
  end

  def build_death_section(death, index)
    time = death["time_formatted"] || "?"
    killed_by = death["killed_by"] || "?"
    assisted = (death["assisted_by"] || []).join(", ")
    zone = death["zone"] || "?"
    zone_detail = death["zone_detail"] || ""
    evitable = death["evitable"] || "?"

    # Player death position
    pos = death["position"]
    elements = ""

    if pos
      px, py = game_to_pixel(pos["x"], pos["y"])
      elements += "<g class='death-marker'>"
      elements += "<circle cx='#{px}' cy='#{py}' r='8' fill='#f1c40f' stroke='#fff' stroke-width='2'/>"
      elements += "<text x='#{px}' y='#{py + 4}' text-anchor='middle' font-size='10' fill='#000' font-weight='bold'>X</text>"
      elements += "<title>#{@meta["champion"]} died here (#{pos["x"]}, #{pos["y"]})</title>"
      elements += "</g>"
    end

    # Nearby enemies
    (death["nearby_enemies"] || []).each_with_index do |enemy, ei|
      coords = zone_to_pixel(enemy["zone"] || enemy["champion"])
      next unless coords
      ex, ey = coords
      # Deterministic offset based on champion name to avoid overlapping
      hash = (enemy["champion"] || "").bytes.sum
      ex += ((hash * 7 + ei * 13) % 31) - 15
      ey += ((hash * 11 + ei * 17) % 31) - 15

      is_killer = enemy["champion"] == killed_by
      color = is_killer ? "#ff4444" : "#cc3333"
      r = is_killer ? 7 : 5

      elements += "<g class='enemy-dot'>"
      elements += "<circle cx='#{ex}' cy='#{ey}' r='#{r}' fill='#{color}' stroke='#fff' stroke-width='1'/>"
      elements += "<title>#{enemy["champion"]} — #{enemy["zone"]}</title>"
      elements += "</g>"
    end

    assist_str = assisted.empty? ? "" : " (+ #{assisted})"
    gold_str = death["gold_unspent"] ? " | #{death["gold_unspent"]}g unspent" : ""

    <<~HTML
      <section class="event-section death-section">
        <div class="event-header">
          <span class="event-time">#{time}</span>
          <span class="event-type death-badge">DEATH ##{index}</span>
        </div>
        <div class="event-body">
          <div class="minimap-container">
            #{minimap_svg(elements)}
          </div>
          <div class="event-details">
            <p><strong>Killed by:</strong> #{killed_by}#{assist_str}</p>
            <p><strong>Zone:</strong> #{zone} — #{zone_detail}</p>
            <p><strong>Avoidable:</strong> #{evitable}#{gold_str}</p>
            #{death["your_items"] ? "<p><strong>Your items:</strong> #{death["your_items"].join(", ")}</p>" : ""}
            #{death["enemy_items"] ? "<p><strong>Enemy items:</strong> #{death["enemy_items"].join(", ")}</p>" : ""}
            <div class="legend">
              <span class="legend-item"><span class="dot gold-dot"></span> You (death)</span>
              <span class="legend-item"><span class="dot red-dot"></span> Enemies</span>
            </div>
          </div>
        </div>
      </section>
    HTML
  end

  def build_teamfight_section(tf, index)
    time = tf["time_formatted"] || "?"
    result = tf["result"] || "?"
    result_class = result == "WON" ? "tf-won" : "tf-lost"
    duration = tf["duration_seconds"] ? "#{tf["duration_seconds"]}s" : "?"
    total_kills = tf["total_kills"] || 0
    your_kills = tf["your_team_kills"] || 0
    enemy_kills = tf["enemy_team_kills"] || 0
    you_died = tf["you_died"] ? "Yes" : "No"
    you_got_kill = tf["you_got_kill"] ? "Yes" : "No"

    # Build kill feed
    kills_html = (tf["kills_detail"] || []).map { |k|
      killer_class = is_your_team?(k["killer"]) ? "kill-ally" : "kill-enemy"
      "<div class='kill-entry #{killer_class}'>#{k["killer"]} killed #{k["victim"]}</div>"
    }.join("\n")

    # Place participants on map using approximate positions
    elements = ""
    placed = {}
    (tf["kills_detail"] || []).each do |k|
      [k["killer"], k["victim"]].each do |champ|
        next if placed[champ]
        placed[champ] = true

        is_ally = is_your_team?(champ)
        is_you = champ == @meta["champion"]
        color = is_you ? "#f1c40f" : (is_ally ? "#4aa3df" : "#e74c3c")
        r = is_you ? 7 : 5

        # Deterministic position: scatter around mid based on champion name hash
        base_x, base_y = 7500, 7500
        hash = (champ || "").bytes.sum
        offset_x = ((hash * 7) % 5001) - 2500
        offset_y = ((hash * 11) % 5001) - 2500
        px, py = game_to_pixel(base_x + offset_x, base_y + offset_y)

        elements += "<g class='tf-dot'>"
        elements += "<circle cx='#{px}' cy='#{py}' r='#{r}' fill='#{color}' stroke='#fff' stroke-width='1'/>"
        elements += "<title>#{champ}</title>"
        elements += "</g>"
      end
    end

    <<~HTML
      <section class="event-section teamfight-section">
        <div class="event-header">
          <span class="event-time">#{time}</span>
          <span class="event-type #{result_class}">TEAMFIGHT ##{index} — #{result}</span>
        </div>
        <div class="event-body">
          <div class="minimap-container small-map">
            #{minimap_svg(elements, 256)}
          </div>
          <div class="event-details">
            <p><strong>Duration:</strong> #{duration} | <strong>Score:</strong> #{your_kills} - #{enemy_kills} (#{total_kills} kills)</p>
            <p><strong>You died:</strong> #{you_died} | <strong>You got kill:</strong> #{you_got_kill}</p>
            <div class="kill-feed">#{kills_html}</div>
            <div class="legend">
              <span class="legend-item"><span class="dot gold-dot"></span> You</span>
              <span class="legend-item"><span class="dot blue-dot"></span> Allies</span>
              <span class="legend-item"><span class="dot red-dot"></span> Enemies</span>
            </div>
          </div>
        </div>
      </section>
    HTML
  end

  def build_objectives_section(objectives)
    return "" if objectives.empty?

    elements = ""
    rows = ""

    objectives.each do |obj|
      monster = obj["monster"] || "?"
      sub = obj["sub_type"] ? " (#{obj["sub_type"]})" : ""
      time = obj["time_formatted"] || "?"
      taken_by = obj["taken_by"] || "?"
      your_zone = obj["your_zone"] || "?"
      present = obj["you_present"] ? "Yes" : "No"
      is_yours = taken_by == "YOUR_TEAM"

      # Place on map
      obj_coords = case monster
        when /Dragon/i then [9866, 4414]
        when /Baron/i then [4966, 10542]
        when /Herald/i then [4966, 10542]
        when /Voidgrubs/i then [4966, 10542]
        else [7500, 7500]
      end
      px, py = game_to_pixel(obj_coords[0], obj_coords[1])
      color = is_yours ? "#4aa3df" : "#e74c3c"

      elements += "<g class='obj-marker'>"
      elements += "<rect x='#{px - 6}' y='#{py - 6}' width='12' height='12' fill='#{color}' stroke='#fff' stroke-width='1' rx='2'/>"
      elements += "<title>#{time} — #{monster}#{sub} (#{taken_by})</title>"
      elements += "</g>"

      row_class = is_yours ? "obj-yours" : "obj-enemy"
      rows += "<tr class='#{row_class}'><td>#{time}</td><td>#{monster}#{sub}</td><td>#{is_yours ? "Your team" : "Enemy"}</td><td>#{your_zone}</td><td>#{present}</td></tr>"
    end

    <<~HTML
      <section class="event-section objectives-section">
        <h2>Objectives</h2>
        <div class="event-body">
          <div class="minimap-container small-map">
            #{minimap_svg(elements, 256)}
          </div>
          <div class="event-details">
            <table class="obj-table">
              <thead><tr><th>Time</th><th>Objective</th><th>Taken by</th><th>Your zone</th><th>Present</th></tr></thead>
              <tbody>#{rows}</tbody>
            </table>
          </div>
        </div>
      </section>
    HTML
  end

  # --- Helpers ---

  def is_your_team?(champion)
    return true if champion == @meta["champion"]
    (@meta["teammates"] || []).any? { |t| t["champion"] == champion }
  end

  def minimap_svg(overlay_elements, size = MAP_SIZE)
    <<~SVG
      <svg width="#{size}" height="#{size}" viewBox="0 0 #{MAP_SIZE} #{MAP_SIZE}" class="minimap-svg">
        <image href="https://ddragon.leagueoflegends.com/cdn/14.24.1/img/map/map11.png"
               x="0" y="0" width="#{MAP_SIZE}" height="#{MAP_SIZE}" preserveAspectRatio="xMidYMid slice"/>
        #{overlay_elements}
      </svg>
    SVG
  end

  # --- Full HTML wrapper ---

  def wrap_html(body)
    match_id = @review.dig("meta", "match_id") || "unknown"
    champion = @meta["champion"] || "?"
    <<~HTML
      <!DOCTYPE html>
      <html lang="en">
      <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>#{champion} Game Review — LoL IA Coaching</title>
        <style>
          :root {
            --bg: #0d1117;
            --surface: #161b22;
            --border: #30363d;
            --text: #c9d1d9;
            --text-dim: #8b949e;
            --gold: #f1c40f;
            --blue: #4aa3df;
            --red: #e74c3c;
            --green: #2ecc71;
          }

          * { box-sizing: border-box; margin: 0; padding: 0; }

          body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
            padding: 20px;
            max-width: 1100px;
            margin: 0 auto;
          }

          h1 { font-size: 1.5em; margin-bottom: 10px; color: #fff; }
          h2 { font-size: 1.3em; margin-bottom: 12px; color: var(--gold); border-bottom: 1px solid var(--border); padding-bottom: 6px; }
          h3 { font-size: 1em; margin-bottom: 8px; color: var(--text-dim); }

          .header-section {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 24px;
            margin-bottom: 20px;
            text-align: center;
          }

          .game-result {
            display: inline-block;
            font-size: 1.2em;
            font-weight: bold;
            padding: 4px 20px;
            border-radius: 4px;
            margin-bottom: 10px;
          }
          .result-win { background: var(--green); color: #000; }
          .result-loss { background: var(--red); color: #fff; }

          .stats-row {
            display: flex;
            gap: 20px;
            justify-content: center;
            flex-wrap: wrap;
            margin: 12px 0;
          }
          .stat { color: var(--text-dim); font-size: 0.95em; }
          .stat strong { color: var(--text); }

          .teams-row {
            display: flex;
            gap: 16px;
            justify-content: center;
            margin-top: 16px;
            flex-wrap: wrap;
          }
          .team-box {
            flex: 1;
            max-width: 400px;
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 12px;
            text-align: left;
          }
          .blue-team-box { border-left: 3px solid var(--blue); }
          .red-team-box { border-left: 3px solid var(--red); }
          .teammate, .enemy-player {
            display: block;
            font-size: 0.85em;
            padding: 2px 0;
            color: var(--text-dim);
          }

          .event-section {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 16px;
          }

          .event-header {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 12px;
          }

          .event-time {
            font-family: 'SF Mono', 'Menlo', monospace;
            font-size: 1.1em;
            color: var(--gold);
            font-weight: bold;
          }

          .event-type {
            font-size: 0.85em;
            font-weight: bold;
            padding: 3px 10px;
            border-radius: 4px;
            text-transform: uppercase;
          }

          .death-badge { background: var(--red); color: #fff; }
          .tf-won { background: var(--green); color: #000; }
          .tf-lost { background: var(--red); color: #fff; }

          .event-body {
            display: flex;
            gap: 20px;
            align-items: flex-start;
            flex-wrap: wrap;
          }

          .event-details {
            flex: 1;
            min-width: 250px;
          }
          .event-details p {
            margin-bottom: 6px;
            font-size: 0.9em;
          }
          .event-desc {
            font-size: 0.88em;
            color: var(--text-dim);
            margin-bottom: 12px;
          }

          .minimap-container {
            flex-shrink: 0;
            border: 2px solid var(--border);
            border-radius: 4px;
            overflow: hidden;
            background: #000;
          }
          .minimap-container.small-map { }

          .minimap-svg { display: block; }
          .minimap-svg circle, .minimap-svg rect { cursor: pointer; }
          .minimap-svg circle:hover, .minimap-svg rect:hover { filter: brightness(1.4); }

          .chart-container {
            display: flex;
            justify-content: center;
            overflow-x: auto;
          }

          .kill-feed {
            margin-top: 8px;
            font-size: 0.85em;
          }
          .kill-entry {
            padding: 2px 8px;
            border-radius: 3px;
            margin-bottom: 2px;
          }
          .kill-ally { background: rgba(74, 163, 223, 0.15); color: var(--blue); }
          .kill-enemy { background: rgba(231, 76, 60, 0.15); color: var(--red); }

          .legend {
            margin-top: 10px;
            display: flex;
            gap: 14px;
            flex-wrap: wrap;
          }
          .legend-item {
            display: flex;
            align-items: center;
            gap: 5px;
            font-size: 0.8em;
            color: var(--text-dim);
          }
          .dot {
            display: inline-block;
            width: 10px;
            height: 10px;
            border-radius: 50%;
          }
          .gold-dot { background: var(--gold); }
          .blue-dot { background: var(--blue); }
          .red-dot { background: var(--red); }

          .obj-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.85em;
          }
          .obj-table th {
            text-align: left;
            padding: 6px 8px;
            border-bottom: 1px solid var(--border);
            color: var(--text-dim);
            font-size: 0.85em;
          }
          .obj-table td {
            padding: 5px 8px;
            border-bottom: 1px solid var(--border);
          }
          .obj-yours td:nth-child(3) { color: var(--blue); }
          .obj-enemy td:nth-child(3) { color: var(--red); }

          footer {
            text-align: center;
            padding: 20px;
            color: var(--text-dim);
            font-size: 0.8em;
          }

          @media (max-width: 700px) {
            .event-body { flex-direction: column; }
            .minimap-container { align-self: center; }
          }
        </style>
      </head>
      <body>
        #{body}
        <footer>Generated by LoL IA Coaching Visualizer</footer>
      </body>
      </html>
    HTML
  end
end
