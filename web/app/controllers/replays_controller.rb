class ReplaysController < ApplicationController
  before_action :require_login
  before_action :set_replay, only: [:show, :review, :minimap, :generate_coaching, :reprocess, :destroy]

  def index
    @replays = current_user.replays.order(created_at: :desc)
  end

  def show
  end

  def create
    @replay = current_user.replays.new(replay_params)
    @replay.status = :pending

    if @replay.save
      # Queue the replay for processing
      ReplayProcessJob.perform_later(@replay.id)
      redirect_to @replay, notice: "Replay uploade ! Analyse en cours..."
    else
      redirect_to replays_path, alert: @replay.errors.full_messages.join(", ")
    end
  end

  def review
    if @replay.completed?
      render json: @replay.review_json
    else
      render json: { status: @replay.status, message: "Analyse en cours..." }
    end
  end

  def minimap
    if @replay.completed? && @replay.review_json.present?
      # Generate HTML visualization on the fly
      render html: generate_minimap_html(@replay).html_safe
    else
      render plain: "Pas encore disponible", status: 202
    end
  end

  def generate_coaching
    if @replay.completed? && @replay.review_json.present?
      CoachingReviewJob.perform_later(@replay.id)
      redirect_to @replay, notice: "Review coaching en cours de generation..."
    else
      redirect_to @replay, alert: "L'analyse doit etre terminee avant de generer la review."
    end
  end

  def reprocess
    @replay.update(status: :queued)
    ReplayProcessJob.perform_later(@replay.id)
    redirect_to @replay, notice: "Re-analyse lancee !"
  end

  def destroy
    @replay.destroy
    redirect_to replays_path, notice: "Replay supprime."
  end

  private

  def set_replay
    @replay = current_user.replays.find(params[:id])
  end

  def replay_params
    params.require(:replay).permit(:match_id, :rofl_file)
  end

  def generate_minimap_html(replay)
    # Write review JSON to temp file for Visualizer
    tmp_path = Rails.root.join("tmp", "#{replay.match_id}_review.json")
    File.write(tmp_path, replay.review_json.to_json)

    visualizer = Visualizer.new(tmp_path.to_s)
    html = visualizer.generate_html
  ensure
    File.delete(tmp_path) if tmp_path && File.exist?(tmp_path)
    html || "<p>Erreur lors de la generation de la minimap.</p>"
  end
end
