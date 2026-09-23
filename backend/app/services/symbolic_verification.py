from __future__ import annotations

"""Fail-closed exact checks for a deliberately small JEE question subset."""

import re
from dataclasses import dataclass

import sympy as sp

from app.schemas.generation import GeneratedQuestion, MachineCheckSpec, SymbolicVerification


_RATIONAL = re.compile(r"^-?\d+(?:/\d+)?$")


class UnsupportedSpecification(ValueError):
    pass


def _rational(value: str) -> sp.Rational:
    value = value.strip()
    if not _RATIONAL.fullmatch(value):
        raise UnsupportedSpecification("only integer or fractional symbolic values are supported")
    return sp.Rational(value)


def _number(value: sp.Expr) -> str:
    value = sp.simplify(value)
    if value.is_Rational:
        return str(value.p) if value.q == 1 else f"{value.p}/{value.q}"
    return str(value)


def _root_set(values: list[sp.Expr]) -> str:
    return "{" + ", ".join(sorted((_number(value) for value in values), key=lambda item: sp.Rational(item))) + "}"


def _normalise_option(value: str) -> str:
    value = value.strip().strip("$")
    value = value.replace("\\left", "").replace("\\right", "").replace("\\,", "")
    return re.sub(r"\s+", "", value).replace("\\{", "{").replace("\\}", "}")


@dataclass(frozen=True)
class VerificationOutcome:
    verification: SymbolicVerification
    correct_answer: str | None = None


def _mcq_answer(question: GeneratedQuestion, spec: MachineCheckSpec, computed: str) -> str:
    if question.question_type.value not in {"single_correct_mcq", "multiple_correct_mcq"}:
        return computed
    if len(question.options) != len(spec.option_values):
        raise UnsupportedSpecification("option values do not correspond to the displayed options")
    if any(_normalise_option(displayed) != _normalise_option(declared)
           for displayed, declared in zip(question.options, spec.option_values, strict=True)):
        raise UnsupportedSpecification("machine-check option values differ from the displayed options")
    matches = [index for index, option in enumerate(spec.option_values) if _normalise_option(option) == _normalise_option(computed)]
    if len(matches) != 1:
        raise UnsupportedSpecification("computed answer does not identify exactly one option")
    return chr(ord("A") + matches[0])


def _display_values_match(question: GeneratedQuestion, spec: MachineCheckSpec) -> None:
    if not spec.display_values:
        raise UnsupportedSpecification("machine-check specification does not declare displayed values")
    displayed = _normalise_option(question.stem)
    missing = [value for value in spec.display_values if _normalise_option(value) not in displayed]
    if missing:
        raise UnsupportedSpecification("machine-check values do not occur in the displayed stem")


def _quadratic(spec: MachineCheckSpec) -> str:
    if len(spec.coefficients) != 3:
        raise UnsupportedSpecification("quadratic checks require [a, b, c] coefficients")
    a, b, c = (_rational(value) for value in spec.coefficients)
    if a == 0:
        raise UnsupportedSpecification("quadratic leading coefficient cannot be zero")
    discriminant = sp.simplify(b**2 - 4 * a * c)
    if discriminant < 0 or not sp.sqrt(discriminant).is_Rational:
        raise UnsupportedSpecification("v1 supports quadratics with rational real roots only")
    roots = [sp.simplify((-b + sp.sqrt(discriminant)) / (2 * a)), sp.simplify((-b - sp.sqrt(discriminant)) / (2 * a))]
    return _root_set(list(set(roots)))


def _polynomial_integral(spec: MachineCheckSpec) -> str:
    if not spec.terms or spec.lower_bound is None or spec.upper_bound is None:
        raise UnsupportedSpecification("integral checks require terms and both bounds")
    x = sp.symbols("x")
    integrand = sum(_rational(term.coefficient) * x ** term.power for term in spec.terms)
    return _number(sp.integrate(integrand, (x, _rational(spec.lower_bound), _rational(spec.upper_bound))))


def _constant_acceleration(spec: MachineCheckSpec) -> str:
    required = {"initial_velocity_mps", "acceleration_mps2", "time_s"}
    if not required <= set(spec.quantities):
        raise UnsupportedSpecification("constant acceleration requires initial_velocity_mps, acceleration_mps2, time_s")
    value = spec.quantities["initial_velocity_mps"] + spec.quantities["acceleration_mps2"] * spec.quantities["time_s"]
    return f"{value:g} m/s"


def _work_done(spec: MachineCheckSpec) -> str:
    required = {"force_n", "displacement_m", "cos_theta"}
    if not required <= set(spec.quantities):
        raise UnsupportedSpecification("work checks require force_n, displacement_m, cos_theta")
    value = spec.quantities["force_n"] * spec.quantities["displacement_m"] * spec.quantities["cos_theta"]
    return f"{value:g} J"


def verify(question: GeneratedQuestion) -> VerificationOutcome:
    spec = question.machine_check
    if spec is None:
        return VerificationOutcome(SymbolicVerification(status="fallback_required", reason="No machine-check specification was supplied."))
    try:
        _display_values_match(question, spec)
        computed = {
            "quadratic_roots": _quadratic,
            "polynomial_definite_integral": _polynomial_integral,
            "constant_acceleration_final_velocity": _constant_acceleration,
            "work_done_by_constant_force": _work_done,
        }[spec.family](spec)
        answer = _mcq_answer(question, spec, computed)
        return VerificationOutcome(
            SymbolicVerification(status="verified", family=spec.family, computed_answer=computed,
                                 checks=["exact answer computed", "answer maps to exactly one displayed option"]),
            correct_answer=answer,
        )
    except (UnsupportedSpecification, KeyError, TypeError, ValueError, ZeroDivisionError) as error:
        return VerificationOutcome(SymbolicVerification(status="fallback_required", family=spec.family, reason=str(error)))
