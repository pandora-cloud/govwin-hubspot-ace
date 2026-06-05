variable "name_prefix" {
  type = string
}

variable "aws_profile" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "ace_catalog" {
  type        = string
  description = "AWS Partner Central catalog: Sandbox (testing) or AWS (production). setup_hubspot seeds the govwin_ace_solution_id dropdown from ListSolutions against this catalog, so it must match the ACE submission Lambdas."
  default     = "Sandbox"
  validation {
    condition     = contains(["Sandbox", "AWS"], var.ace_catalog)
    error_message = "ace_catalog must be Sandbox or AWS."
  }
}

variable "sync_state_table_name" {
  type = string
}

variable "sync_state_table_arn" {
  type = string
}

variable "entity_mappings_table_name" {
  type = string
}

variable "entity_mappings_table_arn" {
  type = string
}

variable "govwin_secret_arn" {
  type = string
}

variable "hubspot_secret_arn" {
  type = string
}

variable "govwin_tokens_secret_arn" {
  type = string
}

variable "govwin_secret_name" {
  type = string
}

variable "hubspot_secret_name" {
  type = string
}

variable "govwin_tokens_secret_name" {
  type = string
}

variable "sns_topic_arn" {
  type = string
}

variable "dlq_url" {
  type = string
}

variable "dlq_arn" {
  type = string
}

variable "govwin_opp_types" {
  type    = string
  default = "ALL"
}

variable "govwin_market" {
  type    = string
  default = ""
}

variable "govwin_saved_search_id" {
  type    = string
  default = ""
}

variable "govwin_bookmarked_only" {
  type    = bool
  default = false
}

variable "govwin_marked_version" {
  type    = string
  default = "2.2"
}

variable "initial_lookback_days" {
  type    = number
  default = 365
}

variable "batch_size" {
  type    = number
  default = 10
}

variable "log_retention_days" {
  type    = number
  default = 30
}
