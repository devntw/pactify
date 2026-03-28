from app.models import Consistency, ContractInput, Obligation, Simulation


def build_obligation(contract: ContractInput, normalized_deadline: str) -> Obligation:
	return Obligation(
		from_party=contract.borrower,
		to_party=contract.lender,
		amount=contract.amount,
		currency=contract.currency,
		deadline=normalized_deadline,
	)


def build_summary(contract: ContractInput, normalized_deadline: str) -> str:
	return f"{contract.borrower} owes {contract.lender} INR {contract.amount} by {normalized_deadline}"


def run_simulations() -> list[Simulation]:
	return [
		Simulation(
			scenario="non_payment",
			result="breach",
			impact=["payment obligation violated"],
		)
	]


def check_consistency(contract: ContractInput) -> Consistency:
	issues: list[str] = []
	if contract.amount <= 0:
		issues.append("amount must be greater than 0")
	if contract.lender == contract.borrower:
		issues.append("lender and borrower must be different")
	return Consistency(valid=len(issues) == 0, issues=issues)
