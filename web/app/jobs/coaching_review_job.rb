class CoachingReviewJob < ApplicationJob
  queue_as :default

  def perform(replay_id)
    replay = Replay.find(replay_id)
    return unless replay.completed? && replay.review_json.present?

    # Load prompt template
    prompt_path = File.expand_path("../../../prompts/review.md", Rails.root)
    prompt_template = File.read(prompt_path)

    # Load champion/role knowledge
    champion = (replay.champion || "").downcase
    role = (replay.role || "").downcase
    prompts_dir = File.expand_path("../../../prompts", Rails.root)

    champion_knowledge = ""
    champion_file = File.join(prompts_dir, "champions", "#{champion}_#{role}.md")
    champion_knowledge = File.read(champion_file) if File.exist?(champion_file)

    role_knowledge = ""
    role_file = File.join(prompts_dir, "roles", "#{role}.md")
    role_knowledge = File.read(role_file) if File.exist?(role_file)

    # Build prompt
    review_json = JSON.pretty_generate(replay.review_json)
    duration_min = (replay.review_json.dig("meta", "duration_seconds") || 0) / 60

    full_prompt = prompt_template
      .gsub("{champion}", replay.champion || "?")
      .gsub("{role}", replay.role || "?")
      .gsub("{enemy_laner}", replay.review_json.dig("meta", "enemy_laner", "champion") || "?")
      .gsub("{result}", replay.result || "?")
      .gsub("{duration}", "#{duration_min} minutes")
      .gsub("{elo}", "Emerald 2")
      .gsub("{json_content}", review_json)
      .gsub("{champion_knowledge}", champion_knowledge)
      .gsub("{role_knowledge}", role_knowledge)

    # Call LLM
    provider = ENV["LLM_PROVIDER"] || "claude"
    review_text = call_llm(provider, full_prompt)

    replay.update!(coaching_review: review_text, llm_provider: provider)
  end

  private

  def call_llm(provider, prompt)
    case provider.downcase
    when "claude"
      call_claude(prompt)
    when "openai"
      call_openai(prompt)
    when "gemini"
      call_gemini(prompt)
    else
      raise "Provider #{provider} not configured. Set LLM_PROVIDER and API key in .env"
    end
  end

  def call_claude(prompt)
    api_key = ENV["ANTHROPIC_API_KEY"] || ENV["CLAUDE_API_KEY"]
    raise "Set ANTHROPIC_API_KEY in .env" unless api_key

    uri = URI("https://api.anthropic.com/v1/messages")
    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = true
    http.read_timeout = 120

    body = {
      model: ENV["CLAUDE_MODEL"] || "claude-sonnet-4-20250514",
      max_tokens: 4096,
      messages: [{ role: "user", content: prompt }]
    }.to_json

    request = Net::HTTP::Post.new(uri)
    request["Content-Type"] = "application/json"
    request["x-api-key"] = api_key
    request["anthropic-version"] = "2023-06-01"
    request.body = body

    response = http.request(request)
    data = JSON.parse(response.body)
    data.dig("content", 0, "text") || "Erreur: #{data}"
  end

  def call_openai(prompt)
    api_key = ENV["OPENAI_API_KEY"]
    raise "Set OPENAI_API_KEY in .env" unless api_key

    uri = URI("https://api.openai.com/v1/chat/completions")
    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = true
    http.read_timeout = 120

    body = {
      model: ENV["OPENAI_MODEL"] || "gpt-4o",
      messages: [{ role: "user", content: prompt }],
      max_tokens: 4096
    }.to_json

    request = Net::HTTP::Post.new(uri)
    request["Content-Type"] = "application/json"
    request["Authorization"] = "Bearer #{api_key}"
    request.body = body

    response = http.request(request)
    data = JSON.parse(response.body)
    data.dig("choices", 0, "message", "content") || "Erreur: #{data}"
  end

  def call_gemini(prompt)
    api_key = ENV["GEMINI_API_KEY"] || ENV["GOOGLE_API_KEY"]
    raise "Set GEMINI_API_KEY in .env" unless api_key

    model = ENV["GEMINI_MODEL"] || "gemini-2.0-flash"
    uri = URI("https://generativelanguage.googleapis.com/v1beta/models/#{model}:generateContent?key=#{api_key}")
    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = true
    http.read_timeout = 120

    body = { contents: [{ parts: [{ text: prompt }] }] }.to_json

    request = Net::HTTP::Post.new(uri)
    request["Content-Type"] = "application/json"
    request.body = body

    response = http.request(request)
    data = JSON.parse(response.body)
    data.dig("candidates", 0, "content", "parts", 0, "text") || "Erreur: #{data}"
  end
end
