class Replay < ApplicationRecord
  belongs_to :user
  has_one :replay_job, dependent: :destroy
  has_one_attached :rofl_file

  enum :status, {
    pending: "pending",
    uploading: "uploading",
    queued: "queued",
    processing: "processing",
    analyzing: "analyzing",
    completed: "completed",
    failed: "failed"
  }

  validates :match_id, presence: true

  def duration_formatted
    return "?" unless review_json&.dig("meta", "duration_seconds")
    mins = review_json["meta"]["duration_seconds"] / 60
    "#{mins}min"
  end
end
