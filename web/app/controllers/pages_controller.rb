class PagesController < ApplicationController
  def home
    redirect_to replays_path if logged_in?
  end
end
