class CreateReplayJobs < ActiveRecord::Migration[8.0]
  def change
    create_table :replay_jobs do |t|
      t.references :replay, null: false, foreign_key: true
      t.string :worker_id
      t.string :status
      t.datetime :started_at
      t.datetime :completed_at
      t.text :error_message

      t.timestamps
    end
  end
end
