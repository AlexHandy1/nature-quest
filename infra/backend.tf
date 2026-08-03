terraform {
  backend "gcs" {
    bucket = "nature-quest-504414-tfstate"
    prefix = "terraform/state"
  }
}
