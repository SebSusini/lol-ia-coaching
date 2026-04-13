class CreateReplays < ActiveRecord::Migration[8.0]
  def change
    create_table :replays do |t|
      t.references :user, null: false, foreign_key: true
      t.string :match_id
      t.string :champion
      t.string :role
      t.string :result
      t.string :status
      t.string :rofl_key
      t.jsonb :review_json
      t.jsonb :positions_json

      t.timestamps
    end
    add_index :replays, :status
  end
end
