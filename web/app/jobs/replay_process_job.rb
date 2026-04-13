class ReplayProcessJob < ApplicationJob
  queue_as :default

  def perform(replay_id)
    replay = Replay.find(replay_id)
    replay.update!(status: :processing)

    # Step 1: Fetch Riot API timeline
    client = RiotApiClient.new
    match_info = client.match_info(replay.match_id)
    timeline = client.match_timeline(replay.match_id)

    timeline_data = { "match_info" => match_info, "timeline" => timeline }

    # Step 2: If .rofl uploaded, queue for worker VM (positions)
    if replay.rofl_file.attached?
      replay.update!(status: :queued)
      # TODO: Send .rofl to worker VM for Frida position scanning
      # For now, proceed without positions
    end

    # Step 3: Run extractors
    replay.update!(status: :analyzing)

    # Write timeline to temp file
    timeline_path = Rails.root.join("tmp", "#{replay.match_id}_timeline.json")
    File.write(timeline_path, timeline_data.to_json)

    # Run extractor
    extractor = Extractor.new(
      timeline_path: timeline_path.to_s,
      summoner_name: replay.user.riot_name
    )
    review = extractor.extract

    # Step 4: Save extraction results
    replay.update!(
      status: :completed,
      champion: review[:meta][:champion],
      role: review[:meta][:role],
      result: review[:meta][:result],
      review_json: review
    )

    # Step 5: Trigger coaching review via LLM
    CoachingReviewJob.perform_later(replay.id)

    # Clean up
    File.delete(timeline_path) if File.exist?(timeline_path)

  rescue => e
    replay&.update(status: :failed)
    replay&.create_replay_job(status: "failed", error_message: e.message)
    raise
  end
end
