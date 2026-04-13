class User < ApplicationRecord
  has_many :replays, dependent: :destroy

  validates :riot_puuid, presence: true, uniqueness: true
  validates :riot_name, presence: true

  def display_name
    "#{riot_name}##{riot_tag}"
  end
end
