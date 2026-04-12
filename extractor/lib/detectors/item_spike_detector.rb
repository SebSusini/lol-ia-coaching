# Detects when you or your lane opponent completes a major item (cost >= 2500g).
# Compares item spike timing between you and the enemy laner.

class ItemSpikeDetector
  MAJOR_ITEM_THRESHOLD = 2500 # gold

  def initialize(context, item_resolver = nil)
    @context = context
    @item_resolver = item_resolver
    @item_data = load_item_data
  end

  def detect
    my_id = @context.my_participant_id
    enemy_laner = find_enemy_laner
    enemy_id = enemy_laner&.dig("participantId")

    purchase_events = @context.timeline_events.select { |e| e["type"] == "ITEM_PURCHASED" }
    undo_events = @context.timeline_events.select { |e| e["type"] == "ITEM_UNDO" }

    # Track item purchases for both players
    my_purchases = purchase_events.select { |e| e["participantId"] == my_id }
    enemy_purchases = enemy_id ? purchase_events.select { |e| e["participantId"] == enemy_id } : []

    # Filter to major items only
    my_spikes = extract_major_items(my_purchases, undo_events, my_id)
    enemy_spikes = extract_major_items(enemy_purchases, undo_events, enemy_id)

    results = []

    # Generate events for your major item completions
    my_spikes.each_with_index do |spike, idx|
      # Compare Nth spike vs opponent's Nth spike
      enemy_nth = enemy_spikes[idx]
      time_advantage = if enemy_nth
        # Positive = you were faster, negative = you were slower
        ((enemy_nth[:timestamp_ms] - spike[:timestamp_ms]) / 1000.0).round(0)
      end

      results << build_event(spike, "YOU", time_advantage, my_purchases, spike[:timestamp_ms])
    end

    # Generate events for enemy major item completions
    enemy_spikes.each_with_index do |spike, idx|
      my_nth = my_spikes[idx]
      time_advantage = if my_nth
        # Positive = enemy was faster, negative = enemy was slower
        ((my_nth[:timestamp_ms] - spike[:timestamp_ms]) / 1000.0).round(0)
      end

      results << build_event(spike, "ENEMY", time_advantage, my_purchases, spike[:timestamp_ms])
    end

    results.sort_by { |e| e[:time_seconds] }
  end

  private

  def find_enemy_laner
    my = @context.participants.find { |p| p["participantId"] == @context.my_participant_id }
    return nil unless my
    @context.participants.find do |p|
      p["teamPosition"] == my["teamPosition"] && p["teamId"] != my["teamId"]
    end
  end

  def extract_major_items(purchases, undo_events, participant_id)
    # Collect undone item IDs for this participant
    undone_item_ids = undo_events
      .select { |e| e["participantId"] == participant_id }
      .map { |e| e["beforeId"] }

    purchases.filter_map do |event|
      item_id = event["itemId"]
      price = item_price(item_id)
      next unless price && price >= MAJOR_ITEM_THRESHOLD
      # Skip if this item was undone
      next if undone_item_ids.include?(item_id)

      {
        item_id: item_id,
        item_name: resolve_item_name(item_id),
        price: price,
        timestamp_ms: event["timestamp"]
      }
    end
  end

  def build_event(spike, who, time_advantage, my_purchases, at_timestamp_ms)
    ts_sec = spike[:timestamp_ms] / 1000.0

    # Find what items the player had at this time
    your_items = my_purchases
      .select { |e| e["timestamp"] <= at_timestamp_ms }
      .map { |e| resolve_item_name(e["itemId"]) }
      .compact

    {
      time_seconds: ts_sec,
      time_formatted: format_time(ts_sec),
      type: "ITEM_SPIKE",
      who: who,
      item_id: spike[:item_id],
      item_name: spike[:item_name],
      item_price: spike[:price],
      your_items_at_time: your_items.last(6),
      time_advantage: time_advantage
    }
  end

  def item_price(item_id)
    item = @item_data[item_id.to_s]
    item&.dig("gold", "total")
  end

  def resolve_item_name(item_id)
    if @item_resolver
      @item_resolver.resolve(item_id)
    else
      @item_data[item_id.to_s]&.dig("name") || "Item ##{item_id}"
    end
  end

  def load_item_data
    cache_file = File.expand_path("../../../.item_cache.json", __dir__)
    if File.exist?(cache_file)
      data = JSON.parse(File.read(cache_file))
      return data["items"] if data["items"]
    end
    {}
  rescue
    {}
  end

  def format_time(seconds)
    minutes = (seconds / 60).to_i
    secs = (seconds % 60).to_i
    "%d:%02d" % [minutes, secs]
  end
end
