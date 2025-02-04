import logging
from pathlib import Path
from typing import Callable, Optional

import pyro
import torch
from pyro import distributions as pdist

from sbibm.tasks.simulator import Simulator
from sbibm.tasks.task import Task


class GaussianMixture(Task):
    def __init__(
        self,
        dim: int = 2,
        prior_bound: float = 10.0,
    ):
        """Gaussian Mixture.

        Inference of mean under uniform prior.

        Args:
            dim: Dimensionality of parameters and data.
            prior_bound: Prior is uniform in [-prior_bound, +prior_bound].
        """
        super().__init__(
            dim_parameters=dim,
            dim_data=dim,
            name=Path(__file__).parent.name,
            name_display="Gaussian Mixture",
            num_observations=10,
            num_posterior_samples=10000,
            num_reference_posterior_samples=10000,
            num_simulations=[100, 1000, 10000, 100000, 1000000],
            path=Path(__file__).parent.absolute(),
        )

        self.prior_params = {
            "low": -prior_bound * torch.ones((self.dim_parameters,)),
            "high": +prior_bound * torch.ones((self.dim_parameters,)),
        }

        self.prior_dist = pdist.Uniform(**self.prior_params).to_event(1)
        self.prior_dist.set_default_validate_args(False)

        self.simulator_params = {
            "mixture_locs_factor": torch.tensor([1.0, 1.0]),
            "mixture_scales": torch.tensor([1.0, 0.1]),
            "mixture_weights": torch.tensor([0.5, 0.5]),
        }

    def get_prior(self) -> Callable:
        def prior(num_samples=1):
            return pyro.sample("parameters", self.prior_dist.expand_by([num_samples]))

        return prior

    def get_simulator(self, max_calls: Optional[int] = None) -> Simulator:
        """Get function returning samples from simulator given parameters

        Args:
            max_calls: Maximum number of function calls. Additional calls will
                result in SimulationBudgetExceeded exceptions. Defaults to None
                for infinite budget

        Return:
            Simulator callable
        """

        def simulator(parameters):
            parameters = torch.atleast_2d(parameters)
            idx = pyro.sample(
                "mixture_idx",
                pdist.Categorical(probs=torch.tile(self.simulator_params["mixture_weights"][None],
                                  (parameters.shape[0], 1)))
            )
            return pyro.sample(
                "data",
                pdist.Normal(
                    loc=self.simulator_params["mixture_locs_factor"][idx][:, None] * parameters,
                    scale=self.simulator_params["mixture_scales"][idx][:, None],
                ),
            )

        return Simulator(task=self, simulator=simulator, max_calls=max_calls)

    def _sample_reference_posterior(
        self,
        num_samples: int,
        num_observation: Optional[int] = None,
        observation: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """Sample reference posterior for given observation

        Uses closed form solution with rejection sampling

        Args:
            num_samples: Number of samples to generate
            num_observation: Observation number
            observation: Instead of passing an observation number, an observation may be
                passed directly

        Returns:
            Samples from reference posterior
        """
        assert not (num_observation is None and observation is None)
        assert not (num_observation is not None and observation is not None)

        if num_observation is not None:
            observation = self.get_observation(num_observation=num_observation)

        log = logging.getLogger(__name__)

        reference_posterior_samples = []

        # Reject samples outside of prior bounds
        counter = 0
        while len(reference_posterior_samples) < num_samples:
            counter += 1
            idx = pyro.sample(
                "mixture_idx",
                pdist.Categorical(self.simulator_params["mixture_weights"]),
            )
            sample = pyro.sample(
                "posterior_sample",
                pdist.Normal(
                    loc=self.simulator_params["mixture_locs_factor"][idx] * observation,
                    scale=self.simulator_params["mixture_scales"][idx],
                ),
            )
            is_outside_prior = torch.isinf(self.prior_dist.log_prob(sample).sum())

            if len(reference_posterior_samples) > 0:
                is_duplicate = sample in torch.cat(reference_posterior_samples)
            else:
                is_duplicate = False

            if not is_outside_prior and not is_duplicate:
                reference_posterior_samples.append(sample)

        reference_posterior_samples = torch.cat(reference_posterior_samples)
        acceptance_rate = float(num_samples / counter)

        log.info(
            f"Acceptance rate for observation {num_observation}: {acceptance_rate}"
        )

        return reference_posterior_samples


if __name__ == "__main__":
    task = GaussianMixture()
    task._setup(n_jobs=-1)
