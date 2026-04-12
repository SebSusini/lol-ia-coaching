require "httparty"
require "json"
require "dotenv/load"

class RiotApiClient
  BASE_URL_PLATFORM = "https://%s.api.riotgames.com"
  BASE_URL_REGION = "https://%s.api.riotgames.com"

  def initialize
    @api_key = ENV.fetch("RIOT_API_KEY")
    @platform = ENV.fetch("RIOT_PLATFORM", "euw1")
    @region = ENV.fetch("RIOT_REGION", "europe")
  end

  # Get match timeline with all events
  def match_timeline(match_id)
    url = "#{region_url}/lol/match/v5/matches/#{match_id}/timeline"
    get(url)
  end

  # Get match info (participants, result, duration)
  def match_info(match_id)
    url = "#{region_url}/lol/match/v5/matches/#{match_id}"
    get(url)
  end

  # Get PUUID from Riot ID
  def account_by_riot_id(game_name, tag_line)
    url = "#{region_url}/riot/account/v1/accounts/by-riot-id/#{game_name}/#{tag_line}"
    get(url)
  end

  # Get recent match IDs for a player
  def match_history(puuid, count: 5)
    url = "#{region_url}/lol/match/v5/matches/by-puuid/#{puuid}/ids?count=#{count}"
    get(url)
  end

  private

  def platform_url
    BASE_URL_PLATFORM % @platform
  end

  def region_url
    BASE_URL_REGION % @region
  end

  def get(url)
    response = HTTParty.get(url, headers: { "X-Riot-Token" => @api_key })

    case response.code
    when 200
      JSON.parse(response.body)
    when 429
      raise "Rate limited by Riot API. Wait and retry."
    when 403
      raise "Invalid or expired Riot API key. Get a new one at https://developer.riotgames.com/"
    else
      raise "Riot API error #{response.code}: #{response.body}"
    end
  end
end
