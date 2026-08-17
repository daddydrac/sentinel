.PHONY: test local preflight fmt validate plan deploy deploy-infra deploy-ui add-user add-users outputs smoke load load-test stage-hf benchmark-100g hpc-benchmark destroy destroy-all bootstrap-state package

TFVARS ?= $(CURDIR)/infra/environments/demo.tfvars

test:
	python3 -m unittest discover -s tests -v

local:
	DEMO_TOKEN=local-demo-token python3 local/server.py --port 8080

preflight:
	bash scripts/preflight.sh

fmt:
	terraform -chdir=infra fmt -recursive

validate:
	terraform -chdir=infra init -backend=false
	terraform -chdir=infra validate

plan: preflight
	terraform -chdir=infra init
	terraform -chdir=infra fmt -check -recursive
	terraform -chdir=infra validate
	terraform -chdir=infra plan -var-file=$(TFVARS) -out=demo.tfplan

deploy:
	bash scripts/deploy_e2e.sh $(TFVARS)

deploy-infra:
	bash scripts/deploy.sh $(TFVARS)

deploy-ui:
	bash scripts/deploy_ui.sh

add-user:
	@test -n "$(EMAIL)" || (echo "EMAIL is required" >&2; exit 1)
	USER_GROUPS="$(or $(GROUPS),approver)" bash scripts/provision_cognito_user.sh "$(EMAIL)" "$(or $(GROUPS),approver)"

add-users:
	@test -n "$(FILE)" || (echo "FILE is required" >&2; exit 1)
	bash scripts/provision_cognito_users.sh "$(FILE)"

outputs:
	terraform -chdir=infra output

smoke:
	bash scripts/smoke_aws.sh

load-test:
	python3 scripts/load_test.py --url "$$(terraform -chdir=infra output -raw demo_url)" --token "$$(terraform -chdir=infra output -raw demo_token)"

load: load-test

stage-hf:
	bash scripts/stage_hf_data.sh

hpc-benchmark:
	bash scripts/run_100g_benchmark.sh

benchmark-100g: hpc-benchmark

destroy:
	bash scripts/destroy.sh $(TFVARS)

destroy-all:
	bash scripts/destroy_all.sh $(TFVARS)

bootstrap-state:
	bash scripts/bootstrap_remote_state.sh

package:
	zip -qrD ../HPC_Autonomous_Agents_GraphRAG_AWS.zip . \
		-x '.git/*' '*/.terraform/*' '*.tfstate*' '*.tfplan' 'infra/lambda.zip' \
		'infra/backend.tf' 'infra/backend.hcl' '*/__pycache__/*' '*/*.pyc' '*/*/*.pyc' \
		'ui/node_modules/*' 'ui/dist/*' 'ui/public/runtime-config.json' 'infra/modules/model_serving/model-context.zip'
