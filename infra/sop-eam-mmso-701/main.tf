terraform {
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 5.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.0" }
  }
}

variable "aws_region" {
  default = "us-east-1"
}

provider "aws" {
  region = var.aws_region
}

# ---------------------------------------------------------------------
# S3 - same bucket/folder names as SOP-EAM-MMSO-701
# ---------------------------------------------------------------------

resource "aws_s3_bucket" "error_bucket" {
  bucket = "eam-mmso-error-bucket-2330"
}

resource "aws_s3_bucket" "keyword_bucket" {
  bucket = "eam-mmso-keyword-bucket-2330"
}

resource "aws_s3_object" "keyword_input_folder" {
  bucket = aws_s3_bucket.keyword_bucket.id
  key    = "input/"
}

resource "aws_s3_object" "keyword_processed_folder" {
  bucket = aws_s3_bucket.keyword_bucket.id
  key    = "processed/"
}

resource "aws_s3_object" "keyword_error_folder" {
  bucket = aws_s3_bucket.keyword_bucket.id
  key    = "error/"
}

# ---------------------------------------------------------------------
# Lambda - one function per SOP step/action
# ---------------------------------------------------------------------

data "archive_file" "classify_failure" {
  type        = "zip"
  source_file = "${path.module}/lambda_src/classify_failure.py"
  output_path = "${path.module}/build/classify_failure.zip"
}

data "archive_file" "move_keyword_files_to_input" {
  type        = "zip"
  source_file = "${path.module}/lambda_src/move_keyword_files_to_input.py"
  output_path = "${path.module}/build/move_keyword_files_to_input.zip"
}

data "archive_file" "verify_job_retry" {
  type        = "zip"
  source_file = "${path.module}/lambda_src/verify_job_retry.py"
  output_path = "${path.module}/build/verify_job_retry.zip"
}

resource "aws_iam_role" "lambda_role" {
  name = "SOP-EAM-MMSO-701-lambda-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda_s3_access" {
  name = "SOP-EAM-MMSO-701-lambda-s3-access"
  role = aws_iam_role.lambda_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:ListBucket", "s3:GetObject", "s3:PutObject", "s3:CopyObject", "s3:DeleteObject"]
      Resource = [
        aws_s3_bucket.error_bucket.arn, "${aws_s3_bucket.error_bucket.arn}/*",
        aws_s3_bucket.keyword_bucket.arn, "${aws_s3_bucket.keyword_bucket.arn}/*",
      ]
    }]
  })
}

resource "aws_lambda_function" "classify_failure" {
  function_name    = "SOP-EAM-MMSO-701-classify-failure"
  role             = aws_iam_role.lambda_role.arn
  handler          = "classify_failure.handler"
  runtime          = "python3.12"
  timeout          = 30
  filename         = data.archive_file.classify_failure.output_path
  source_code_hash = data.archive_file.classify_failure.output_base64sha256

  environment {
    variables = {
      ERROR_BUCKET   = aws_s3_bucket.error_bucket.id
      KEYWORD_BUCKET = aws_s3_bucket.keyword_bucket.id
    }
  }
}

resource "aws_lambda_function" "move_keyword_files_to_input" {
  function_name    = "SOP-EAM-MMSO-701-move-keyword-files-to-input"
  role             = aws_iam_role.lambda_role.arn
  handler          = "move_keyword_files_to_input.handler"
  runtime          = "python3.12"
  timeout          = 30
  filename         = data.archive_file.move_keyword_files_to_input.output_path
  source_code_hash = data.archive_file.move_keyword_files_to_input.output_base64sha256

  environment {
    variables = {
      KEYWORD_BUCKET = aws_s3_bucket.keyword_bucket.id
    }
  }
}

resource "aws_lambda_function" "verify_job_retry" {
  function_name    = "SOP-EAM-MMSO-701-verify-job-retry"
  role             = aws_iam_role.lambda_role.arn
  handler          = "verify_job_retry.handler"
  runtime          = "python3.12"
  timeout          = 30
  filename         = data.archive_file.verify_job_retry.output_path
  source_code_hash = data.archive_file.verify_job_retry.output_base64sha256

  environment {
    variables = {
      ERROR_BUCKET = aws_s3_bucket.error_bucket.id
    }
  }
}

# ---------------------------------------------------------------------
# Step Function - mirrors the SOP's decision_tree exactly
# ---------------------------------------------------------------------

resource "aws_iam_role" "sfn_role" {
  name = "SOP-EAM-MMSO-701-sfn-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "sfn_invoke_lambda" {
  name = "SOP-EAM-MMSO-701-sfn-invoke-lambda"
  role = aws_iam_role.sfn_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "lambda:InvokeFunction"
      Resource = [
        aws_lambda_function.classify_failure.arn,
        aws_lambda_function.move_keyword_files_to_input.arn,
        aws_lambda_function.verify_job_retry.arn,
      ]
    }]
  })
}

resource "aws_sfn_state_machine" "enrollments_mms_job_remediation" {
  name     = "SOP-EAM-MMSO-701-EnrollmentsMMSJobRemediation"
  role_arn = aws_iam_role.sfn_role.arn

  definition = jsonencode({
    Comment = "SOP-EAM-MMSO-701 - Enrollments MMS job failure remediation"
    StartAt = "ClassifyFailure"
    States = {

      # STEP 1: Classify the Enrollments MMS job failure by step number and pending keyword files
      "ClassifyFailure" = {
        Type       = "Task"
        Resource   = aws_lambda_function.classify_failure.arn
        ResultPath = "$.classification"
        Next       = "CheckFailureConditions"
      }

      "CheckFailureConditions" = {
        Type = "Choice"
        Choices = [
          {
            # A) step < 5000 AND error/ has file(s) -> move_keyword_files_to_input (auto)
            And = [
              { Variable = "$.classification.failed_step", NumericLessThan = 5000 },
              { Variable = "$.classification.keyword_error_files_present", BooleanEquals = true },
            ]
            Next = "MoveKeywordFilesToInput"
          },
          {
            # B) step < 5000 AND error/ is empty -> no_action_required (auto)
            And = [
              { Variable = "$.classification.failed_step", NumericLessThan = 5000 },
              { Variable = "$.classification.keyword_error_files_present", BooleanEquals = false },
            ]
            Next = "NoActionRequired"
          },
        ]
        # C) step >= 5000, either case -> manual_review (human)
        Default = "ManualReview"
      }

      "NoActionRequired" = {
        Type    = "Pass"
        Comment = "Early-stage failure, nothing pending in error/ - no remediation needed."
        End     = true
      }

      "ManualReview" = {
        Type    = "Pass"
        Comment = "Late-stage failure (step >= 5000) - always needs human review."
        End     = true
      }

      "MoveKeywordFilesToInput" = {
        Type       = "Task"
        Resource   = aws_lambda_function.move_keyword_files_to_input.arn
        ResultPath = "$.moveResult"
        Next       = "WaitForNextRun"
      }

      # STEP 2: Verify remediation outcome
      "WaitForNextRun" = {
        Type    = "Wait"
        Seconds = 60
        Next    = "VerifyRetry"
      }

      "VerifyRetry" = {
        Type       = "Task"
        Resource   = aws_lambda_function.verify_job_retry.arn
        ResultPath = "$.retryResult"
        Next       = "CheckRetryOutcome"
      }

      "CheckRetryOutcome" = {
        Type = "Choice"
        Choices = [
          {
            Variable     = "$.retryResult.job_run_state"
            StringEquals = "SUCCEEDED"
            Next         = "LogSuccess"
          },
        ]
        # Repeated failure - escalate, do not auto-retry a second time
        Default = "ManualReviewRepeatedFailure"
      }

      "LogSuccess" = {
        Type = "Pass"
        End  = true
      }

      "ManualReviewRepeatedFailure" = {
        Type    = "Pass"
        Comment = "job_run_state != SUCCEEDED after retry - escalate to data-engineering@company.com."
        End     = true
      }
    }
  })
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.enrollments_mms_job_remediation.arn
}

output "error_bucket" {
  value = aws_s3_bucket.error_bucket.id
}

output "keyword_bucket" {
  value = aws_s3_bucket.keyword_bucket.id
}
