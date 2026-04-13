Rails.application.routes.draw do
  root "pages#home"

  # Auth
  get "/auth/riot/callback", to: "auth#callback"
  delete "/logout", to: "auth#destroy"

  # Replays
  resources :replays, only: [:index, :show, :create, :destroy] do
    member do
      get :review
      get :minimap
      post :generate_coaching
      post :reprocess
    end
  end

  # API
  namespace :api do
    resources :reviews, only: [:show]
  end

  # Health check
  get "up" => "rails/health#show", as: :rails_health_check
end
