class AuthController < ApplicationController
  # GET /auth/riot/callback?name=Banditacciu&tag=EUW
  # Dev mode: simple find_or_create by name+tag
  # Production: this would use Riot RSO OAuth2 flow
  def callback
    name = params[:name]
    tag = params[:tag]

    unless name.present? && tag.present?
      redirect_to root_path, alert: "Parametres manquants (name, tag)"
      return
    end

    # Generate a deterministic pseudo-PUUID from name+tag for dev
    puuid = "dev-#{Digest::SHA256.hexdigest("#{name}##{tag}")[0..31]}"

    user = User.find_or_create_by!(riot_puuid: puuid) do |u|
      u.riot_name = name
      u.riot_tag = tag
      u.region = "EUW1"
    end

    # Update name/tag in case they changed
    user.update!(riot_name: name, riot_tag: tag)

    session[:user_id] = user.id
    redirect_to replays_path, notice: "Connecte en tant que #{user.display_name}"
  end

  # DELETE /logout
  def destroy
    session.delete(:user_id)
    redirect_to root_path, notice: "Deconnecte !"
  end
end
