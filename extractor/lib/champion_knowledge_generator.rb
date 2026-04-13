require "net/http"
require "json"
require "uri"

# Generates champion knowledge using an LLM API.
# Falls back to a structured template if no API key is configured.
class ChampionKnowledgeGenerator
  TEMPLATE = <<~TEMPLATE
    # %{champion} %{role_cap} - Champion Knowledge Block

    ## Identity
    [TODO] Type (assassin/mage/bruiser/tank/enchanter/ADC/AP jungler/etc.), playstyle, difficulty.

    %{role_section}

    ## Power Spikes
    - **Level 3:** [TODO]
    - **Level 6:** [TODO]
    - **1st item (%{first_item}):** [TODO]
    - **2 items:** [TODO]
    - **3 items:** [TODO]

    ## Item Build
    - **Core:** [TODO]
    - **Situational:** [TODO]
    - **Boots:** [TODO]
    - **Runes:** [TODO]
    - **Summoners:** [TODO]

    ## Key Abilities
    - **Passive:** [TODO]
    - **Q:** [TODO]
    - **W:** [TODO]
    - **E:** [TODO]
    - **R:** [TODO]

    ## Combos
    - **Short trade:** [TODO]
    - **All-in:** [TODO]
    - **Teamfight:** [TODO]

    ## Teamfight Role
    [TODO] How to play teamfights, positioning, target priority.

    ## Matchups
    - **Favorable:** [TODO]
    - **Difficult:** [TODO]

    ## Common Mistakes
    1. [TODO]
    2. [TODO]
    3. [TODO]
    4. [TODO]
    5. [TODO]
  TEMPLATE

  JUNGLE_SECTION = <<~SECTION
    ## Clear Path
    - **Standard full clear:** [TODO]
    - **Alternative clear:** [TODO]
    - **Clear tip:** [TODO]

    ## Gank Patterns
    - **Pre-6:** [TODO]
    - **Post-6:** [TODO]

    ## Objective Control
    [TODO] Dragon/Herald/Baron priority, solo potential.
  SECTION

  LANE_SECTION = <<~SECTION
    ## Wave Management
    [TODO] Early wave strategy, when to push/freeze, recall timings.

    ## Roaming
    [TODO] When to roam, conditions, priority targets.
  SECTION

  LLM_PROMPT = <<~PROMPT
    Generate a champion knowledge document for %{champion} %{role} in League of Legends (season 2026 / current patch).

    Use the EXACT format below. Fill every section with practical, actionable coaching advice.
    Write in English. Use LoL terms in English. Be concise — no filler, no "it depends", just concrete info.
    Focus on what a Gold-Emerald player needs to know to play this champion well.

    Format to follow (fill the [...] sections):

    # %{champion} %{role_cap} - Champion Knowledge Block

    ## Identity
    [1-2 sentences: archetype, playstyle, scaling profile, difficulty]

    %{role_prompt_section}

    ## Power Spikes
    - **Level 3:** [when they become functional and why]
    - **Level 6:** [ult impact]
    - **1st item ([name]):** [what it enables]
    - **2 items:** [powerlevel at 2 items]
    - **3 items:** [late game spike if relevant]

    ## Item Build
    - **Core:** [item1 > item2 > item3]
    - **Situational:** [when to deviate]
    - **Boots:** [default + alternatives]
    - **Runes:** [primary + secondary tree, key choices]
    - **Summoners:** [default combo + when to change]

    ## Key Abilities
    - **Passive:** [what it does and how to use it optimally]
    - **Q:** [usage tips]
    - **W:** [usage tips]
    - **E:** [usage tips]
    - **R:** [usage tips]

    ## Combos
    - **Short trade:** [input sequence]
    - **All-in:** [input sequence]
    - **Teamfight:** [how to sequence abilities in fights]

    ## Teamfight Role
    [2-3 sentences: positioning, target priority, ability usage]

    ## Matchups
    - **Favorable:** [3-5 champions and brief why]
    - **Difficult:** [3-5 champions and brief why]

    ## Common Mistakes
    1. [most common mistake for this champion]
    2. [second most common]
    3. [third]
    4. [fourth]
    5. [fifth]
  PROMPT

  JUNGLE_PROMPT_SECTION = <<~SECTION
    ## Clear Path
    - **Standard full clear:** [camp order with smite usage]
    - **Alternative clear:** [when and why]
    - **Clear tip:** [the #1 thing to optimize clear speed]

    ## Gank Patterns
    - **Pre-6:** [when to gank or farm pre-6]
    - **Post-6:** [how ganks change with ult]

    ## Objective Control
    [Dragon/Herald/Baron priority, solo potential, timing windows]
  SECTION

  LANE_PROMPT_SECTION = <<~SECTION
    ## Wave Management
    [Early wave strategy: push/freeze/slow push, recall timings, when to manipulate waves]

    ## Roaming
    [When to roam, what triggers a roam, priority lanes]
  SECTION

  def initialize(champion, role)
    @champion = champion
    @role = role.downcase
    @role_cap = @role == "jungle" ? "Jungle" : case @role
      when "mid", "middle" then "Mid"
      when "top" then "Top"
      when "bot", "bottom", "adc" then "ADC"
      when "support", "utility" then "Support"
      else @role.capitalize
    end
  end

  def generate
    api_key = detect_api_key
    if api_key
      generate_with_llm(api_key)
    else
      generate_template
    end
  end

  private

  def detect_api_key
    provider = ENV["LLM_PROVIDER"] || "claude"
    case provider.downcase
    when "claude"
      key = ENV["ANTHROPIC_API_KEY"] || ENV["CLAUDE_API_KEY"]
      return { provider: "claude", key: key } if key && !key.empty?
    when "openai"
      key = ENV["OPENAI_API_KEY"]
      return { provider: "openai", key: key } if key && !key.empty?
    when "gemini"
      key = ENV["GEMINI_API_KEY"] || ENV["GOOGLE_API_KEY"]
      return { provider: "gemini", key: key } if key && !key.empty?
    when "mistral"
      key = ENV["MISTRAL_API_KEY"]
      return { provider: "mistral", key: key } if key && !key.empty?
    when "ollama"
      return { provider: "ollama", key: "local" }
    end

    # Try all providers as fallback
    %w[ANTHROPIC_API_KEY CLAUDE_API_KEY OPENAI_API_KEY GEMINI_API_KEY GOOGLE_API_KEY MISTRAL_API_KEY].each do |env_var|
      key = ENV[env_var]
      if key && !key.empty?
        provider = case env_var
          when /ANTHROPIC|CLAUDE/ then "claude"
          when /OPENAI/ then "openai"
          when /GEMINI|GOOGLE/ then "gemini"
          when /MISTRAL/ then "mistral"
        end
        return { provider: provider, key: key }
      end
    end

    nil
  end

  def generate_with_llm(api_config)
    prompt = build_llm_prompt
    $stderr.puts "  Using #{api_config[:provider]} API..."

    content = case api_config[:provider]
    when "claude" then call_claude(prompt, api_config[:key])
    when "openai" then call_openai(prompt, api_config[:key])
    when "gemini" then call_gemini(prompt, api_config[:key])
    when "mistral" then call_mistral(prompt, api_config[:key])
    when "ollama" then call_ollama(prompt)
    end

    # Clean up: remove markdown code fences if the LLM wrapped the output
    content = content.gsub(/\A```(?:markdown)?\n/, "").gsub(/\n```\z/, "").strip

    # Validate the content has substance (not just headers)
    if content.length < 200
      $stderr.puts "  LLM response too short, falling back to template."
      return generate_template
    end

    content
  end

  def generate_template
    role_section = @role == "jungle" ? JUNGLE_SECTION : LANE_SECTION
    first_item = "[TODO: first item name]"

    TEMPLATE % {
      champion: @champion,
      role_cap: @role_cap,
      role_section: role_section.strip,
      first_item: first_item
    }
  end

  def build_llm_prompt
    role_section = @role == "jungle" ? JUNGLE_PROMPT_SECTION : LANE_PROMPT_SECTION

    LLM_PROMPT % {
      champion: @champion,
      role: @role,
      role_cap: @role_cap,
      role_prompt_section: role_section.strip
    }
  end

  # --- LLM API Calls ---

  def call_claude(prompt, api_key)
    uri = URI("https://api.anthropic.com/v1/messages")
    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = true
    http.read_timeout = 30

    body = {
      model: "claude-sonnet-4-20250514",
      max_tokens: 3000,
      messages: [{ role: "user", content: prompt }]
    }.to_json

    request = Net::HTTP::Post.new(uri)
    request["Content-Type"] = "application/json"
    request["x-api-key"] = api_key
    request["anthropic-version"] = "2023-06-01"
    request.body = body

    response = http.request(request)
    data = JSON.parse(response.body)
    data.dig("content", 0, "text") || raise("Claude API error: #{data}")
  end

  def call_openai(prompt, api_key)
    uri = URI("https://api.openai.com/v1/chat/completions")
    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = true
    http.read_timeout = 30

    body = {
      model: "gpt-4o",
      messages: [{ role: "user", content: prompt }],
      max_tokens: 3000
    }.to_json

    request = Net::HTTP::Post.new(uri)
    request["Content-Type"] = "application/json"
    request["Authorization"] = "Bearer #{api_key}"
    request.body = body

    response = http.request(request)
    data = JSON.parse(response.body)
    data.dig("choices", 0, "message", "content") || raise("OpenAI API error: #{data}")
  end

  def call_gemini(prompt, api_key)
    model = "gemini-2.0-flash"
    uri = URI("https://generativelanguage.googleapis.com/v1beta/models/#{model}:generateContent?key=#{api_key}")
    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = true
    http.read_timeout = 30

    body = {
      contents: [{ parts: [{ text: prompt }] }]
    }.to_json

    request = Net::HTTP::Post.new(uri)
    request["Content-Type"] = "application/json"
    request.body = body

    response = http.request(request)
    data = JSON.parse(response.body)
    data.dig("candidates", 0, "content", "parts", 0, "text") || raise("Gemini API error: #{data}")
  end

  def call_mistral(prompt, api_key)
    uri = URI("https://api.mistral.ai/v1/chat/completions")
    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = true
    http.read_timeout = 30

    body = {
      model: "mistral-large-latest",
      messages: [{ role: "user", content: prompt }],
      max_tokens: 3000
    }.to_json

    request = Net::HTTP::Post.new(uri)
    request["Content-Type"] = "application/json"
    request["Authorization"] = "Bearer #{api_key}"
    request.body = body

    response = http.request(request)
    data = JSON.parse(response.body)
    data.dig("choices", 0, "message", "content") || raise("Mistral API error: #{data}")
  end

  def call_ollama(prompt)
    uri = URI("http://localhost:11434/api/generate")
    http = Net::HTTP.new(uri.host, uri.port)
    http.read_timeout = 60

    body = {
      model: "llama3",
      prompt: prompt,
      stream: false
    }.to_json

    request = Net::HTTP::Post.new(uri)
    request["Content-Type"] = "application/json"
    request.body = body

    response = http.request(request)
    data = JSON.parse(response.body)
    data["response"] || raise("Ollama error: #{data}")
  end
end
