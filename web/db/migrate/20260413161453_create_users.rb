class CreateUsers < ActiveRecord::Migration[8.0]
  def change
    create_table :users do |t|
      t.string :riot_puuid
      t.string :riot_name
      t.string :riot_tag
      t.string :region

      t.timestamps
    end
  end
end
