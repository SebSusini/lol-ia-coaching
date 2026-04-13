# Load the extractor library from the parent project
extractor_lib = File.expand_path("../../../extractor/lib", __dir__)

if Dir.exist?(extractor_lib)
  $LOAD_PATH.unshift(extractor_lib) unless $LOAD_PATH.include?(extractor_lib)

  # Require the key classes so they're available to jobs
  require "riot_api_client"
  require "extractor"
  require "game_context"
  require "item_resolver"

  Rails.logger.info "[LoL Coaching] Extractor lib loaded from #{extractor_lib}" if defined?(Rails.logger) && Rails.logger
end

# Load dotenv from parent project for API keys
parent_env = File.expand_path("../../../.env", __dir__)
if File.exist?(parent_env)
  File.readlines(parent_env).each do |line|
    line.strip!
    next if line.empty? || line.start_with?("#")
    key, value = line.split("=", 2)
    ENV[key] = value unless ENV[key] # Don't override existing env vars
  end
end
