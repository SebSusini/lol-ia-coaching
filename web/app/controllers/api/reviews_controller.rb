class Api::ReviewsController < ApplicationController
  def show
    replay = Replay.find(params[:id])

    if replay.completed?
      render json: {
        review_json: replay.review_json,
        coaching_review: replay.coaching_review,
        status: replay.status
      }
    else
      render json: { status: replay.status, message: "Analyse en cours..." }
    end
  end
end
