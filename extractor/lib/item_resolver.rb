require "net/http"
require "json"
require "uri"

# Resolves item IDs to names using Riot Data Dragon
class ItemResolver
  CACHE_FILE = File.expand_path("../../.item_cache.json", __dir__)
  DDRAGON_URL = "https://ddragon.leagueoflegends.com"

  def initialize
    @items = load_cache || fetch_and_cache
  end

  def resolve(item_id)
    return nil if item_id.nil? || item_id == 0
    @items[item_id.to_s]&.dig("name") || "Item ##{item_id}"
  end

  def resolve_list(item_ids)
    item_ids.map { |id| resolve(id) }.compact
  end

  private

  def load_cache
    return nil unless File.exist?(CACHE_FILE)

    data = JSON.parse(File.read(CACHE_FILE))
    # Cache for 7 days
    if data["cached_at"] && (Time.now.to_i - data["cached_at"]) < 7 * 24 * 3600
      data["items"]
    end
  rescue
    nil
  end

  def fetch_and_cache
    # Get latest version
    versions_url = "#{DDRAGON_URL}/api/versions.json"
    versions = JSON.parse(Net::HTTP.get(URI(versions_url)))
    latest = versions.first

    # Get items
    items_url = "#{DDRAGON_URL}/cdn/#{latest}/data/en_US/item.json"
    items_data = JSON.parse(Net::HTTP.get(URI(items_url)))
    items = items_data["data"]

    # Cache
    File.write(CACHE_FILE, JSON.generate({ cached_at: Time.now.to_i, items: items }))
    items
  rescue => e
    $stderr.puts "Warning: Could not fetch item data: #{e.message}"
    {}
  end
end
