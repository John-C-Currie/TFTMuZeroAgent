import time
import config
import datetime
import ray
import os
import copy
import gymnasium as gym
import numpy as np
from storage import Storage
from global_buffer import GlobalBuffer
from Simulator.tft_simulator import TFT_Simulator, parallel_env, env as tft_env
from Models.replay_buffer_wrapper import BufferWrapper
from ray.tune.registry import register_env
from pettingzoo.test import parallel_api_test, api_test
from Simulator import utils

from Models.MCTS_torch import MCTS
from Models.MuZero_torch_agent import MuZeroNetwork as TFTNetwork
import Models.MuZero_torch_trainer as MuZero_trainer
from torch.utils.tensorboard import SummaryWriter
import torch


# Can add scheduling_strategy="SPREAD" to ray.remote. Not sure if it makes any difference
@ray.remote(num_gpus=config.GPU_SIZE_PER_WORKER)
class DataWorker(object):
    def __init__(self, rank, global_buffer):
        self.agent_network = TFTNetwork()
        self.rank = rank
        self.ckpt_time = time.time()
        self.env = parallel_env()
        self.buffer = BufferWrapper(global_buffer)

    '''
    Description -
        Each worker runs one full game and will restart after the game finishes. At the end of the game, 
        it will fetch a new agent. 
    Inputs -   
        env 
            A parallel environment of our tft simulator. This is the game that the worker interacts with.
        buffers
            One buffer wrapper that holds a series of buffers that we store all information required to train in.
        global_buffer
            A buffer that all the individual game buffers send their information to.
        storage
            An object that stores global information like the weights of the global model and current training progress
        weights
            Weights of the initial model for the agent to play the game with.
    '''
    def collect_gameplay_experience(self, weights):
        self.agent_network.set_weights(weights)
        agent = MCTS(self.agent_network)
        self.ckpt_time = time.time()
        # Reset the environment
        player_observation = self.env.reset()
        # This is here to make the input (1, observation_size) for initial_inference
        player_observation = self.observation_to_input(player_observation)
        # Used to know when players die and which agent is currently acting
        terminated = {player_id: False for player_id in self.env.possible_agents}

        # While the game is still going on.
        while not all(terminated.values()):
            # Ask our model for an action and policy
            actions, policy, string_samples, board_map, directive  = agent.policy(player_observation)
            step_actions = self.getStepActions(terminated, actions, board_map, directive)
            storage_actions = utils.decode_action(actions)

            # Take that action within the environment and return all of our information for the next player
            next_observation, reward, terminated, _, info = self.env.step(step_actions)
            # print(terminated)
            # store the action for MuZero
            for i, key in enumerate(terminated.keys()):
                if not info[key]["state_empty"]:
                    # Store the information in a buffer to train on later.
                    self.buffer.store_replay_buffer(key, player_observation[0][i], storage_actions[i],
                                                        reward[key], policy[i], string_samples[i])

            # Set up the observation for the next action
            player_observation = self.observation_to_input(next_observation)

        # weights = copy.deepcopy(ray.get(storage).get_model())
        # agent.network.set_weights(weights)
        # self.rank += config.CONCURRENT_GAMES
        self.buffer.store_global_buffer()
        self.buffer.reset()
        if config.DEBUG:
            print(f'Worker {self.rank} finished a game in {(time.time() - self.ckpt_time)/60} minutes. Max AVG traversed depth: {agent.max_depth_search / agent.runs}')
        return self.rank

    '''
    Description -
        Turns the actions from a format that is sent back from the model to a format that is usable by the environment.
        We check for any new dead agents in this method as well.
    Inputs
        terminated
            A dictionary of player_ids and booleans that tell us if a player can execute an action or not.
        actions
            The set of actions returned from the model in a list of strings format
    Returns
        step_actions
            A dictionary of player_ids and actions usable by the environment.
    '''
    def getStepActions(self, terminated, actions, board_map, directive):
        step_actions = {}
        i = 0
        for player_id, terminate in terminated.items():
            if not terminate:
                step_actions[player_id] = [self.decode_action_to_one_hot(actions[i]), board_map[i], directive[i]]
                i += 1
        return step_actions

    '''
    Description -
        Turns a dictionary of player observations into a list of list format that the model can use.
    '''
    def observation_to_input(self, observation):
        tensors = []
        masks = []
        for obs in observation.values():
            tensors.append(obs["tensor"])
            masks.append(obs["mask"])
        return [np.asarray(tensors), masks]

    '''
    Description -
        Turns a string action into a series of one_hot lists that can be used in the step_function.
        More specifics on what every list means can be found in the step_function.
    '''

    def decode_action_to_one_hot(self, str_action):
        num_items = str_action.count("_")
        split_action = str_action.split("_")
        element_list = [0, 0, 0, 0]
        for i in range(num_items + 1):
            element_list[i] = int(split_action[i])

        # decoded_action = np.zeros(config.ACTION_DIM[0] + config.ACTION_DIM[1] + config.ACTION_DIM[2])
        # decoded_action[0:7] = utils.one_hot_encode_number(element_list[0], 7)

        # if element_list[0] == 1:
        #     decoded_action[7:12] = utils.one_hot_encode_number(element_list[1], 5)

        # if element_list[0] == 2:
        #     decoded_action[7:44] = utils.one_hot_encode_number(element_list[1], 37) + \
        #                            utils.one_hot_encode_number(element_list[2], 37)

        # if element_list[0] == 3:
        #     decoded_action[7:44] = utils.one_hot_encode_number(element_list[1], 37)
        #     decoded_action[44:54] = utils.one_hot_encode_number(element_list[2], 10)

        # if element_list[0] == 4:
        #     decoded_action[7:44] = utils.one_hot_encode_number(element_list[1], 37)
        return element_list

    '''
    Description -
        Loads in a set of agents from checkpoints of the users choice to play against each other. These agents all have
        their own policy function and are not required to be the same model. This method is used for evaluating the
        skill level of the current agent and to see how well our agents are training. Metrics from this method are 
        stored in the storage class because this is the data worker side so there are intended to be multiple copies
        of this method running at once. 
    '''
    def evaluate_agents(self, env, storage):
        agents = {"player_" + str(r): MCTS(TFTNetwork())
                  for r in range(config.NUM_PLAYERS)}
        agents["player_1"].network.tft_load_model(1000)
        agents["player_2"].network.tft_load_model(2000)
        agents["player_3"].network.tft_load_model(3000)
        agents["player_4"].network.tft_load_model(4000)
        agents["player_5"].network.tft_load_model(5000)
        agents["player_6"].network.tft_load_model(6000)
        agents["player_7"].network.tft_load_model(7000)

        while True:
            # Reset the environment
            player_observation = env.reset()
            # This is here to make the input (1, observation_size) for initial_inference
            player_observation = np.asarray(
                list(player_observation.values()))
            # Used to know when players die and which agent is currently acting
            terminated = {
                player_id: False for player_id in env.possible_agents}
            # Current action to help with MuZero
            placements = {
                player_id: 0 for player_id in env.possible_agents}
            current_position = 7
            info = {player_id: {"player_won": False}
                    for player_id in env.possible_agents}
            # While the game is still going on.
            while not all(terminated.values()):
                # Ask our model for an action and policy
                actions = {agent: 0 for agent in agents.keys()}
                for i, [key, agent] in enumerate(agents.items()):
                    action, _ = agent.policy(np.expand_dims(player_observation[i], axis=0))
                    actions[key] = action

                # step_actions = self.getStepActions(terminated, np.asarray(actions))

                # Take that action within the environment and return all of our information for the next player
                next_observation, reward, terminated, _, info = env.step(actions)

                # Set up the observation for the next action
                player_observation = np.asarray(list(next_observation.values()))

                for key, terminate in terminated.items():
                    if terminate:
                        placements[key] = current_position
                        current_position -= 1

            for key, value in info.items():
                if value["player_won"]:
                    placements[key] = 0
            storage.record_placements.remote(placements)
            print("recorded places {}".format(placements))
            self.rank += config.CONCURRENT_GAMES


class AIInterface:

    def __init__(self):
        ...

    '''
    Global train model method. This is what gets called from main.
    '''
    def train_torch_model(self, starting_train_step=0):
        gpus = torch.cuda.device_count()
        # ray.init(num_gpus=gpus, num_cpus=config.NUM_CPUS)
        ray.init(num_gpus=gpus)

        current_time = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        train_log_dir = 'logs/gradient_tape/' + current_time + '/train'
        train_summary_writer = SummaryWriter(train_log_dir)
        train_step = starting_train_step

        # storage = Storage.remote(train_step)
        storage = Storage(train_step)
        global_buffer = GlobalBuffer.remote(storage)

        global_agent = TFTNetwork()
        global_agent_weights = storage.get_target_model()
        global_agent.set_weights(global_agent_weights)
        global_agent.to("cuda")

        trainer = MuZero_trainer.Trainer(global_agent)

        # env = parallel_env()

        data_workers = [DataWorker.remote(agent_num, global_buffer) for agent_num in range(config.CONCURRENT_GAMES)]
        weights = storage.get_target_model()
        workers = [worker.collect_gameplay_experience.remote(weights) for worker in data_workers]
        while True:
            while True:
                with open("run.txt", "r") as file:
                    if file.readline() == "0":
                        input("run = 0, press enter to continue and change run to 1")
                    else:
                        print("Continuing training")
                        break
            done, workers = ray.wait(workers)
            rank = ray.get(done)[0]
            while ray.get(global_buffer.available_batch.remote()):
                print("Starting training")
                gameplay_experience_batch = ray.get(global_buffer.sample_batch.remote())
                trainer.train_network(gameplay_experience_batch, global_agent, train_step, train_summary_writer)
                # storage.set_trainer_busy.remote(False)
                storage.set_target_model(global_agent.get_weights())
                train_step += 1
                if train_step % config.CHECKPOINT_STEPS == 0:
                    storage.set_model()
                    global_agent.tft_save_model(train_step)
                print("Finished training")
            print(f'Spawning agent {rank}')
            weights = storage.get_target_model()
            workers.extend([data_workers[rank].collect_gameplay_experience.remote(weights)])
            

    '''
    Method used for testing the simulator. It does not call any AI and generates random actions from numpy. Intended
    to test how fast the simulator is and if there are any bugs that can be caught via multiple runs.
    '''
    def collect_dummy_data(self):
        env = parallel_env()
        while True:
            _ = env.reset()
            terminated = {player_id: False for player_id in env.possible_agents}
            t = time.time_ns()
            rewards = None
            while not all(terminated.values()):
                # agent policy that uses the observation and info
                action = {
                    agent: env.action_space(agent).sample()
                    for agent in env.agents
                    if (agent in terminated and not terminated[agent])
                }
                observation_list, rewards, terminated, truncated, info = env.step(action)
            print("A game just finished in time {}".format(time.time_ns() - t))

    '''
    The global side to the evaluator. Creates a set of workers to test a series of agents.
    '''
    def evaluate(self):
        os.environ["TF_FORCE_GPU_ALLOW_GROWTH"] = "true"
        # gpus = tf.config.list_physical_devices('GPU')
        ray.init(num_gpus=4, num_cpus=16)
        storage = Storage.remote(0)

        env = parallel_env()

        workers = []
        data_workers = [DataWorker.remote(rank) for rank in range(config.CONCURRENT_GAMES)]
        for i, worker in enumerate(data_workers):
            workers.append(worker.evaluate_agents.remote(env, storage))
            time.sleep(1)

        ray.get(workers)

    '''
    PettingZoo's api tests for the simulator.
    '''
    def testEnv(self):
        raw_env = tft_env()
        api_test(raw_env, num_cycles=100000)
        local_env = parallel_env()
        parallel_api_test(local_env, num_cycles=100000)

    '''
    Creates the TFT environment for the PPO model.
    '''
    def env_creator(self, cfg):
        return TFT_Simulator(cfg)
