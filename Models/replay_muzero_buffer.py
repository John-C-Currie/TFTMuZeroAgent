import numpy as np
import config
import random
from global_buffer import GlobalBuffer
from Models.MCTS_Util import split_sample_set


class ReplayBuffer:
    def __init__(self, g_buffer: GlobalBuffer):
        self.gameplay_experiences = []
        self.rewards = []
        self.policy_distributions = []
        self.string_samples = []
        self.action_history = []
        self.g_buffer = g_buffer
        self.combats = []

    def reset(self):
        self.gameplay_experiences = []
        self.rewards = []
        self.policy_distributions = []
        self.string_samples = []
        self.action_history = []
        self.combats = []

    def store_replay_buffer(self, observation, action, reward, policy, string_samples):
        # Records a single step of gameplay experience
        # First few are self-explanatory
        # done is boolean if game is done after taking said action
        self.gameplay_experiences.append(observation)
        self.action_history.append(action)
        self.rewards.append(reward)
        self.policy_distributions.append(policy)
        self.string_samples.append(string_samples)

    def store_combats_buffer(self, combat):
        self.combats.append(combat)

    def get_prev_action(self):
        if self.action_history:
            return self.action_history[-1]
        else:
            return 9

    def get_reward_sequence(self):
        return self.rewards
    
    def set_reward_sequence(self, rewards):
        self.rewards = rewards

    def get_len(self):
        return len(self.gameplay_experiences)

    def store_global_buffer(self, max_length):
        # Putting this if case here in case the episode length is less than 72 which is 8 more than the batch size
        # In general, we are having episodes of 200 or so but the minimum possible is close to 20
        samples_per_player = config.SAMPLES_PER_PLAYER \
            if (len(self.gameplay_experiences) - config.UNROLL_STEPS) > config.SAMPLES_PER_PLAYER \
            else len(self.gameplay_experiences) - config.UNROLL_STEPS
        if samples_per_player > 0:
            # config.UNROLL_STEPS because I don't want to sample the very end of the range
            samples = random.sample(range(0, len(self.gameplay_experiences) - config.UNROLL_STEPS), samples_per_player)
            num_steps = len(self.gameplay_experiences)
            reward_correction = []
            prev_reward = 0
            # for reward in self.rewards:
            #     reward_correction.append(reward - prev_reward)
            #     prev_reward = reward
            for sample in samples:
                # Hard coding because I would be required to do a transpose if I didn't
                # and that takes a lot of time.
                action_set = []
                value_mask_set = []
                reward_mask_set = []
                policy_mask_set = []
                value_set = []
                reward_set = []
                policy_set = []
                sample_set = []

                for current_index in range(sample, sample + config.UNROLL_STEPS + 1):
                    ratio = max_length / num_steps
                    value = self.rewards[-1] * (config.DISCOUNT ** (max_length - (current_index * ratio)))

                    # for i, reward in enumerate(reward_correction[current_index:]):
                    #     value += reward * config.DISCOUNT ** i

                    reward_mask = 0
                    # reward_mask = 1.0 if current_index > sample else 0.0
                    if current_index < num_steps:
                        if current_index != sample:
                            action_set.append(np.asarray(self.action_history[current_index]))
                        else:
                            # To weed this out later when sampling the global buffer
                            action_set.append([0, 0, 0, 0])
                        value_mask_set.append(1.0)
                        reward_mask_set.append(reward_mask)
                        policy_mask_set.append(1.0)
                        value_set.append(value)
                        # This is current_index - 1 in the Google's code but in my version
                        # This is simply current_index since I store the reward with the same time stamp
                        reward_set.append(0.0)
                        policy_set.append(self.policy_distributions[current_index])
                        sample_set.append(self.string_samples[current_index])
                    elif current_index == num_steps:
                        action_set.append(np.asarray(self.action_history[current_index]))
                        value_mask_set.append(1.0)
                        reward_mask_set.append(reward_mask)
                        policy_mask_set.append(1.0)
                        value_set.append(self.rewards[-1])
                        reward_set.append(0.0)
                        policy_set.append(self.policy_distributions[current_index])
                        sample_set.append(self.string_samples[current_index])
                    else:
                        # States past the end of games is treated as absorbing states.
                        action_set.append([0, 0, 0, 0])
                        value_mask_set.append(0.0)
                        reward_mask_set.append(0.0)
                        policy_mask_set.append(0.0)
                        value_set.append(0.0)
                        reward_set.append(0.0)
                        policy_set.append(self.policy_distributions[0])
                        sample_set.append(self.string_samples[0])

                for i in range(len(sample_set)):
                    split_mapping, split_policy = split_sample_set(sample_set[i], policy_set[i])
                    sample_set[i] = split_mapping
                    policy_set[i] = split_policy

                # print(f'{self.rewards[-1]} placement, {value_set}')
                output_sample_set = [self.gameplay_experiences[sample], action_set, value_mask_set, reward_mask_set,
                                     policy_mask_set, value_set, reward_set, policy_set, sample_set]
                self.g_buffer.store_replay_sequence.remote(output_sample_set)
        self.g_buffer.store_combat_sequence.remote(self.combats)
