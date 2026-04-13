class AddCoachingReviewToReplays < ActiveRecord::Migration[8.0]
  def change
    add_column :replays, :coaching_review, :text
    add_column :replays, :llm_provider, :string
  end
end
