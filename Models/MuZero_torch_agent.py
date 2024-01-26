import torch
import config
import collections
import numpy as np
import time
import os

NetworkOutput = collections.namedtuple(
    'NetworkOutput',
    'value reward policy_logits hidden_state')

def dcord_to_2dcord(dcord):
        x = dcord % 7
        y = (dcord - x) // 7
        return x, y

def action_to_3d(action):
    cube_action = np.zeros((action.shape[0], 1, 4))
    # cube_action = np.zeros((action.shape[0], 7, 4, 7))
    for i in range(action.shape[0]):
        cube_action[i] = action[i]
    #     action_selector = np.argmax(action[i][0])
    #     if action_selector == 0:
    #         cube_action[0,:,:] = np.ones((1,4,7))
    #     elif action_selector == 1:
    #         champ_shop_target = np.argmax(action[i][2])
    #         if champ_shop_target < 5:
    #             cube_action[1, champ_shop_target, 9] = 1
    #     elif action_selector == 2:
    #         champ1 = dcord_to_2dcord(action[i][1])
    #         cube_action[2, champ1[0], champ1[1]] = 1
    #         champ2 = dcord_to_2dcord(action[i][2])
    #         cube_action[2, champ2[0], champ2[1]] = 1
    #     elif action_selector == 3:
    #         champ1 = dcord_to_2dcord(action[i][1])
    #         cube_action[3, champ1[0], champ1[1]] = 1
    #         cube_action[3, 5, action[i][2]] = 1
    #     elif action_selector == 4:
    #         champ1 = dcord_to_2dcord(action[i][1])
    #         cube_action[4, champ1[0], champ1[1]] = 1
    #     elif action_selector == 5:
    #         cube_action[5,:,:] = np.ones((1,4,7))
    #     elif action_selector == 6:
    #         cube_action[6,:,:] = np.ones((1,4,7))
    return cube_action

def dict_to_cpu(dictionary):
    cpu_dict = {}
    for key, value in dictionary.items():
        if isinstance(value, torch.Tensor):
            cpu_dict[key] = value.cpu()
        elif isinstance(value, dict):
            cpu_dict[key] = dict_to_cpu(value)
        else:
            cpu_dict[key] = value
    return cpu_dict


class AbstractNetwork(torch.nn.Module):
    def __init__(self):
        super().__init__()
        pass

    def initial_inference(self, observation):
        pass

    def recurrent_inference(self, encoded_state, action):
        pass

    def get_weights(self):
        return dict_to_cpu(self.state_dict())

    def set_weights(self, weights):
        self.load_state_dict(weights)
        self.eval()

    # Renaming as to not override built-in functions
    def tft_save_model(self, episode):
        if not os.path.exists("./Checkpoints"):
            os.makedirs("./Checkpoints")

        path = f'./Checkpoints/checkpoint_{episode}'
        torch.save(self.state_dict(), path)

    # Renaming as to not override built-in functions
    def tft_load_model(self, episode):
        path = f'./Checkpoints/checkpoint_{episode}'
        if os.path.isfile(path):
            self.load_state_dict(torch.load(path))
            self.eval()
            print("Loading model episode {}".format(episode))
        else:
            print("Initializing model with new weights.")


class MuZeroNetwork(AbstractNetwork):
    def __init__(self):
        super().__init__()
        self.full_support_size = config.ENCODER_NUM_STEPS

        # self.representation_network = mlp(config.OBSERVATION_SIZE, [config.LAYER_HIDDEN_SIZE] *
        #                                   config.N_HEAD_HIDDEN_LAYERS, config.HIDDEN_STATE_SIZE)

        self.representation_network = RepNetwork(28, [256] * 16, 1, config.HIDDEN_STATE_SIZE).cuda()

        # self.action_encodings = mlp(config.ACTION_CONCAT_SIZE, [config.LAYER_HIDDEN_SIZE] * 0,
        #                             config.HIDDEN_STATE_SIZE)
        
        self.dynamics_network = DynNetwork(28, [256] * 16, 1, self.full_support_size).cuda()

        self.prediction_network = PredNetwork(28, [256] * 16, 1, self.full_support_size).cuda()

        # self.value_encoder = ValueEncoder(*tuple(map(inverse_contractive_mapping, (-300., 300.))), 0)

        # self.reward_encoder = ValueEncoder(*tuple(map(inverse_contractive_mapping, (-300., 300.))), 0)

        self.board_generator = BoardGenerator(128).cuda()

        self.directive_generator = DirectiveGenerator(128, 58).cuda()

    def prediction(self, encoded_state):
        # print("encoded", encoded_state.shape)
        policy_logits, value = self.prediction_network(encoded_state)
        return policy_logits, value

    def representation(self, observation):
        encoded_state = self.representation_network(observation)
        # Scale encoded state between [0, 1] (See appendix paper Training)
        min_encoded_state = encoded_state.min(1, keepdim=True)[0]
        max_encoded_state = encoded_state.max(1, keepdim=True)[0]
        scale_encoded_state = max_encoded_state - min_encoded_state
        scale_encoded_state[scale_encoded_state < 1e-5] += 1e-5
        encoded_state_normalized = (
                                           encoded_state - min_encoded_state
                                   ) / scale_encoded_state
        return encoded_state_normalized

    def dynamics(self, hidden_state, action):
        cube_action = torch.from_numpy(action_to_3d(action)).to('cuda')

        # for cell, states in zip(self.dynamics_hidden_state_network, lstm_state):
        #     inputs, new_states = cell(inputs, states)
        #     new_nested_states.append([inputs, new_states])
        
        # print("RESULT", c0[0].size())
        # print("RESULT", c0[1].size())
        # print("RESULT", c0[2].size())
        # print("RESULT", c0[3].size())
        # print("PASS", inputs.size())

        # print("SIZE", new_nested_states.size())
        # next_hidden_state = self.rnn_to_flat(new_nested_states)  # (8, 1024) ##DOUBLE CHECK THIS

        # print("NEXT HIDDEN", next_hidden_state.size())
        # reward = self.dynamics_reward_network(next_hidden_state)

        next_hidden_state, reward = self.dynamics_network(hidden_state, cube_action)

        # Scale encoded state between [0, 1] (See paper appendix Training)
        min_next_hidden_state = next_hidden_state.min(1, keepdim=True)[0]
        max_next_hidden_state = next_hidden_state.max(1, keepdim=True)[0]
        scale_next_hidden_state = max_next_hidden_state - min_next_hidden_state
        scale_next_hidden_state[scale_next_hidden_state < 1e-5] += 1e-5
        next_hidden_state_normalized = (
                                                next_hidden_state - min_next_hidden_state
                                        ) / scale_next_hidden_state

        return next_hidden_state_normalized, reward

    def initial_inference(self, observation):
        observation_tensor = torch.from_numpy(observation).float().cuda()
        hidden_state = self.representation(observation_tensor)
        policy_logits, value_logits = self.prediction(hidden_state)
        directive = self.directive_generator(observation_tensor)
        board_distribution = self.board_generator(observation_tensor)

        reward = np.zeros(observation.shape[0])

        # value = self.value_encoder.decode(value_logits.detach().cpu().numpy())
        value = value_logits
        # reward_logits = self.reward_encoder.encode(reward)

        outputs = {
            "value": value,
            "reward": reward,
            "policy_logits": policy_logits,
            "hidden_state": hidden_state
        }
        return outputs, directive, board_distribution

    @staticmethod
    def rnn_to_flat(state):
        """Maps LSTM state to flat vector."""
        states = []
        for cell_state in state:
            states.extend(cell_state)
        return torch.cat(states, dim=-1)

    @staticmethod
    def flat_to_lstm_input(state):
        """Maps flat vector to LSTM state."""
        tensors = []
        cur_idx = 0
        for size in config.RNN_SIZES:
            states = (state[Ellipsis, cur_idx:cur_idx + size],
                      state[Ellipsis, cur_idx + size:cur_idx + 2 * size])

            cur_idx += 2 * size
            tensors.append(states)
        # assert cur_idx == state.shape[-1]
        return tensors

    def recurrent_inference(self, hidden_state, action):
        next_hidden_state, reward_logits = self.dynamics(hidden_state, action)
        policy_logits, value_logits = self.prediction(next_hidden_state)

        # value = self.value_encoder.decode(value_logits.detach().cpu().numpy())
        value = value_logits
        # reward = self.reward_encoder.decode(reward_logits.detach().cpu().numpy())
        reward = np.zeros(hidden_state.shape[0])

        outputs = {
            "value": value,
            "reward": reward,
            "policy_logits": policy_logits,
            "hidden_state": next_hidden_state
        }
        return outputs

def mlp(input_size,
        layer_sizes,
        output_size,
        output_activation=torch.nn.Identity,
        activation=torch.nn.LeakyReLU):
    sizes = [input_size] + layer_sizes + [output_size]
    layers = []
    for i in range(len(sizes) - 1):
        act = activation if i < len(sizes) - 2 else output_activation
        layers += [torch.nn.Linear(sizes[i], sizes[i + 1]), act()]
    return torch.nn.Sequential(*layers).cuda()


class BoardGenerator(torch.nn.Module):

    def __init__(self, ngf) -> torch.nn.Module:
        # Input -> Unit Mask  (batch, obs.shape, 1, 1)
        # Output -> Board Distribution -> (batch, 58, 4, 7)
        super(BoardGenerator, self).__init__()
        self.main = torch.nn.Sequential(
            # input is Z, going into a convolution
            torch.nn.ConvTranspose2d(config.OBSERVATION_SIZE, ngf * 8, (2,2), (1,1), bias=False),
            torch.nn.BatchNorm2d(ngf * 8),
            torch.nn.LeakyReLU(0.2,True),
            # NoiseLayer(),
            # state size. ``(ngf*8) x 4 x 4``
            torch.nn.ConvTranspose2d(ngf * 8, ngf * 8, (1,2), (1,1), (0,0), bias=False),
            torch.nn.BatchNorm2d(ngf * 8),
            torch.nn.LeakyReLU(0.2,True),
            # NoiseLayer(),
            # state size. ``(ngf*4) x 8 x 8``
            torch.nn.ConvTranspose2d( ngf * 8, ngf * 4, (2,2), (1,1), (0,0), bias=False),
            torch.nn.BatchNorm2d(ngf * 4),
            torch.nn.LeakyReLU(0.2,True),
            # NoiseLayer(),
            # state size. ``(ngf*4) x 8 x 8``
            torch.nn.ConvTranspose2d( ngf * 4, ngf * 4, (1,2), (1,1), (0,0), bias=False),
            torch.nn.BatchNorm2d(ngf * 4),
            torch.nn.LeakyReLU(0.2,True),
            # NoiseLayer(),
            # state size. ``(ngf*2) x 16 x 16``
            torch.nn.ConvTranspose2d( ngf * 4, ngf * 2, (2,2), (1,1), (0,0), bias=False),
            torch.nn.BatchNorm2d(ngf * 2),
            torch.nn.LeakyReLU(0.2,True),
            # NoiseLayer(),
            # state size. ``(ngf) x 32 x 32``
            torch.nn.ConvTranspose2d( ngf * 2, 58*1, (1,2), (1,1), (0,0), bias=False),
            torch.nn.LeakyReLU()
            # state size. ``58 x 4 x 7``
        )
    
    def forward(self, input):
        return self.main(input)

    def __call__(self, x):
        return self.forward(x)
    
class DirectiveGenerator(torch.nn.Module):
    def __init__(self, ndf, n_units):
        # Input = (batch, 58*3+1+1+1+1+1+1+1+58, 1)
        # Output =  (batch, 58)
        super(DirectiveGenerator, self).__init__()
        self.main = torch.nn.Sequential(
            # TODO: Encode items and extra data``
            # torch.nn.Conv1d(58*3+58+1+1+1+1+1+1+1, ndf, 1, 1, 0, bias=False),
            torch.nn.Linear(config.OBSERVATION_SIZE, ndf),
            torch.nn.LeakyReLU(inplace=True),

            # torch.nn.Conv1d(ndf, ndf * 2, 1, 1, 0, bias=False),
            torch.nn.Linear(ndf, ndf * 2),
            # torch.nn.BatchNorm1d(ndf * 2),
            torch.nn.LeakyReLU(inplace=True),

            # torch.nn.Conv1d(ndf * 2, ndf * 4, 1, 1, 0, bias=False),
            torch.nn.Linear(ndf * 2, ndf * 4),
            # torch.nn.BatchNorm1d(ndf * 4),
            torch.nn.LeakyReLU(inplace=True),

            # torch.nn.Conv1d(ndf * 4, ndf * 8, 1, 1, 0, bias=False),
            torch.nn.Linear(ndf * 4, ndf * 8),
            # torch.nn.BatchNorm1d(ndf * 8),
            torch.nn.LeakyReLU(inplace=True),

            # torch.nn.Flatten(),
            torch.nn.Linear(ndf * 8, n_units),
            torch.nn.Sigmoid()
        )

    def forward(self, input):
        # input = torch.squeeze(input, dim=2)
        # return self.main(input)
    
        input = torch.squeeze(input)
        return self.main(input)
    
    def __call__(self, x):
        return self.forward(x)

class PredNetwork(torch.nn.Module):
    def __init__(self, input_size, layer_sizes, output_size, encoding_size) -> torch.nn.Module:
        super().__init__()

        # self.resnet = resnet(input_size, layer_sizes, output_size)
        # self.conv_value = torch.nn.Conv1d(256, 3, 1)
        # self.bn_value = torch.nn.BatchNorm1d(3)
        # self.conv_policy = torch.nn.Conv1d(256, 3, 1)
        # self.bn_policy = torch.nn.BatchNorm1d(3)
        self.relu = torch.nn.LeakyReLU(inplace=True)
        self.sigmoid = torch.nn.Sigmoid()
        # self.fc_internal_v = torch.nn.Linear(9, 64)
        # self.fc_value = mlp(64, [config.LAYER_HIDDEN_SIZE] * config.N_HEAD_HIDDEN_LAYERS, 1, output_activation=torch.nn.LeakyReLU)
        # self.fc_internal_p = torch.nn.Linear(9, 64)
        # self.fc_policy = MultiMlp(64, [config.LAYER_HIDDEN_SIZE] * config.N_HEAD_HIDDEN_LAYERS, config.POLICY_HEAD_SIZES)
        hidden = config.HIDDEN_STATE_SIZE

        self.dense1 = torch.nn.Linear(hidden, hidden)
        self.dense2 = torch.nn.Linear(hidden, hidden)
        self.dense3 = torch.nn.Linear(hidden, hidden)
        self.dense4 = torch.nn.Linear(hidden, hidden)
        self.dense5 = torch.nn.Linear(hidden, hidden)
        self.dense6 = torch.nn.Linear(hidden, hidden)
        self.dense7 = torch.nn.Linear(hidden, hidden)
        self.dense8 = torch.nn.Linear(hidden, hidden)
        self.value_dense1 = torch.nn.Linear(hidden, hidden)
        self.value_dense2 = torch.nn.Linear(hidden, hidden)
        self.value_dense3 = torch.nn.Linear(hidden, hidden)
        self.value_dense4 = torch.nn.Linear(hidden, 1)
        self.policy_dense1 = torch.nn.Linear(hidden, hidden)
        self.policy_dense2 = torch.nn.Linear(hidden, hidden)
        self.policy_dense3 = torch.nn.Linear(hidden, hidden)
        self.policy_dense4 = torch.nn.Linear(hidden, 4)
        self.softmax = torch.nn.Softmax(dim=1)

    def forward(self, x):
        # x = self.resnet(x)

        # value = self.conv_value(x)
        # value = self.bn_value(value)
        # value = self.relu(value)
        # value = torch.flatten(value, start_dim=1)
        # value = self.fc_internal_v(value)
        # value = self.relu(value)
        # value = self.fc_value(value)

        # policy = self.conv_policy(x)
        # policy = self.bn_policy(policy)
        # policy = self.relu(policy)
        # policy = torch.flatten(policy, start_dim=1)
        # policy = self.fc_internal_p(policy)
        # policy = self.relu(policy)
        # policy = self.fc_policy(policy)
        
        x = torch.squeeze(x)
        x = self.relu(self.dense1(x))
        x = self.relu(self.dense2(x)) + x
        x = self.relu(self.dense3(x)) + x
        x = self.relu(self.dense4(x)) + x
        x = self.relu(self.dense5(x)) + x
        x = self.dense6(x)

        policy = self.relu(self.policy_dense1(x)) + x
        policy = self.relu(self.policy_dense2(x)) + policy
        policy = self.relu(self.policy_dense3(x)) + policy
        policy = self.softmax(self.sigmoid(self.policy_dense4(x)))
        # print(policy)

        value = self.relu(self.value_dense1(x)) + x
        value = self.relu(self.value_dense2(x)) + value
        value = self.relu(self.value_dense3(x)) + value
        value = self.relu(self.value_dense4(x))

        return policy, value

    def __call__(self, x):
        return self.forward(x)

class RepNetwork(torch.nn.Module):
    def __init__(self, input_size, layer_sizes, output_size, encoding_size) -> torch.nn.Module:
        super().__init__()
        hidden = config.HIDDEN_STATE_SIZE

        # self.conv1 = torch.nn.Conv1d(239, 256, kernel_size=1, stride=1, padding=1, bias=False)
        # self.bn = torch.nn.BatchNorm1d(256)
        self.relu = torch.nn.LeakyReLU(inplace=True)
        # self.resnet = resnet(input_size, layer_sizes, output_size)
        self.dense1 = torch.nn.Linear(config.OBSERVATION_SIZE, hidden)
        # self.dropout1 = torch.nn.Dropout(0.5)
        self.dense2 = torch.nn.Linear(hidden, hidden)
        self.dense3 = torch.nn.Linear(hidden, hidden)
        self.dense4 = torch.nn.Linear(hidden, hidden)
        self.dense5 = torch.nn.Linear(hidden, hidden)
        self.dense6 = torch.nn.Linear(hidden, hidden)

    def forward(self, x):
        # print(x.shape)
        # x = torch.squeeze(x, dim=2)
        # x = self.conv1(x)
        # x = self.bn(x)
        # x = self.relu(x)
        # x = self.resnet(x)

        x = torch.squeeze(x)
        x = self.relu(self.dense1(x))
        x = self.relu(self.dense2(x)) + x
        x = self.relu(self.dense3(x)) + x
        x = self.relu(self.dense4(x)) + x
        x = self.relu(self.dense5(x)) + x
        x = self.dense6(x)

        return x

    def __call__(self, x):
        return self.forward(x)
    
class DynNetwork(torch.nn.Module):
    def __init__(self, input_size, layer_sizes, output_size, encoding_size) -> torch.nn.Module:
        super().__init__()
        hidden = config.HIDDEN_STATE_SIZE

        # self.conv1 = torch.nn.Conv1d(257, 256, kernel_size=3, stride=1, padding=1, bias=False)
        # self.bn = torch.nn.BatchNorm1d(256)
        self.relu = torch.nn.LeakyReLU(inplace=True)
        # self.conv_reward = torch.nn.Conv1d(256, 1, 1)
        # self.bn_reward = torch.nn.BatchNorm1d(1)
        # self.fc_reward = mlp(3, [config.LAYER_HIDDEN_SIZE] * config.N_HEAD_HIDDEN_LAYERS, 1, output_activation=torch.nn.LeakyReLU)
        # self.resnet = resnet(input_size, layer_sizes, output_size)
        self.dense1 = torch.nn.Linear(hidden + config.ACTION_ENCODING_SIZE, hidden)
        # self.dropout1 = torch.nn.Dropout(0.5)
        self.dense2 = torch.nn.Linear(hidden, hidden)
        self.dense3 = torch.nn.Linear(hidden, hidden)
        self.dense4 = torch.nn.Linear(hidden, hidden)
        self.dense5 = torch.nn.Linear(hidden, hidden)
        self.dense6 = torch.nn.Linear(hidden, hidden)
        self.single_reward = torch.nn.Linear(hidden, 1)

    def forward(self, x, action):
        # x = torch.squeeze(x, dim=2)
        # state = torch.concatenate((x, action), dim=1).type(torch.cuda.FloatTensor)
        # x = self.conv1(state)
        # x = self.bn(x)
        # x = self.relu(x)
        # x = self.resnet(x)
        # new_state = x

        # reward = self.conv_reward(x)
        # reward = self.bn_reward(reward)
        # flat =  torch.flatten(reward, start_dim=1)
        # reward = self.fc_reward(flat)

        x = torch.squeeze(x)
        action = torch.squeeze(action)
        x = torch.concatenate((x, action), dim=1).type(torch.cuda.FloatTensor)
        x = self.relu(self.dense1(x))
        x = self.relu(self.dense2(x)) + x
        x = self.relu(self.dense3(x)) + x
        x = self.relu(self.dense4(x)) + x
        x = self.relu(self.dense5(x)) + x
        new_state = self.dense6(x)

        reward = self.relu(self.single_reward(x))

        return new_state, reward

    def __call__(self, x, action):
        return self.forward(x, action)
    
    @staticmethod
    def flat_to_lstm_input(state):
        """Maps flat vector to LSTM state."""
        tensors = []
        cur_idx = 0
        for size in config.RNN_SIZES:
            states = (state[Ellipsis, cur_idx:cur_idx + size],
                      state[Ellipsis, cur_idx + size:cur_idx + 2 * size])

            cur_idx += 2 * size
            tensors.append(states)
        # assert cur_idx == state.shape[-1]
        return tensors
    
    @staticmethod
    def rnn_to_flat(state):
        """Maps LSTM state to flat vector."""
        states = []
        for cell_state in state:
            states.extend(cell_state)
        return torch.cat(states, dim=-1)

def resnet(input_size,
        layer_sizes,
        output_size):
    sizes = [input_size] + layer_sizes + [output_size]
    layers = []
    for i in range(0, len(sizes) - 1):
        layers += [ResLayer(sizes[i], sizes[i + 1])]
    
    return torch.nn.Sequential(*layers).cuda()

# Cursed? Idk
# Linear(input, layer_size) -> RELU
#      -> Linear -> Identity -> 0
#      -> Linear -> Identity -> 1
#      ... for each size in output_size
#  -> output -> [0, 1, ... n]
class MultiMlp(torch.nn.Module):
    def __init__(self,
                 input_size,
                 layer_sizes,
                 output_sizes,
                 output_activation=torch.nn.Identity,
                 activation=torch.nn.LeakyReLU):
        super().__init__()

        sizes = [input_size] + layer_sizes
        layers = []
        for i in range(len(sizes) - 1):
            act = activation
            layers += [torch.nn.Linear(sizes[i], sizes[i + 1]), act()]
        self.encoding_layer = torch.nn.Sequential(*layers).cuda()

        # self.output_heads = []

        self.head_0 = torch.nn.Sequential(
                torch.nn.Linear(layer_sizes[-1], output_sizes[0])
            ).cuda()
        
        # self.head_1 = torch.nn.Sequential(
        #         torch.nn.Linear(layer_sizes[-1], output_sizes[1]),
        #         output_activation()
        #     ).cuda()
        
        # self.head_2 = torch.nn.Sequential(
        #         torch.nn.Linear(layer_sizes[-1], output_sizes[2]),
        #         output_activation()
        #     ).cuda()
        
        # self.head_3 = torch.nn.Sequential(
        #         torch.nn.Linear(layer_sizes[-1], output_sizes[3]),
        #         output_activation()
        #     ).cuda()
        
        # self.head_4 = torch.nn.Sequential(
        #         torch.nn.Linear(layer_sizes[-1], output_sizes[4]),
        #         output_activation()
        #     ).cuda()

        # for size in output_sizes:
        #     output_layer = torch.nn.Sequential(
        #         torch.nn.Linear(layer_size[-1], size),
        #         output_activation()
        #     ).cuda()
        #     self.output_heads.append(output_layer)

    def forward(self, x):
        # Encode the hidden state
        x = self.encoding_layer(x)

        # Pass x into all output heads
        output = []

        output.append(self.head_0(x))
        # output.append(self.head_1(x))
        # output.append(self.head_2(x))
        # output.append(self.head_3(x))
        # output.append(self.head_4(x))

        # return torch.cat(output, dim=-1)
        return output

    def __call__(self, x):
        return self.forward(x)
    
def conv1x1(in_planes: int, out_planes: int, stride: int = 1) -> torch.nn.Conv2d:
    """1x1 convolution"""
    return torch.nn.Conv2d(in_planes, out_planes, kernel_size=1, stride=stride, bias=False)

class ResLayer(torch.nn.Module):
    def __init__(self, input_channels, n_kernels) -> torch.nn.Module:
        super().__init__()

        self.conv1 = torch.nn.Conv1d(256, 256, kernel_size=3, stride=1, padding=1, bias=False)
        self.bn1 = torch.nn.BatchNorm1d(256)
        self.conv2 = torch.nn.Conv1d(256, 256, kernel_size=3, stride=1, padding=1, bias=False)
        self.bn2 = torch.nn.BatchNorm1d(256)
        self.relu = torch.nn.LeakyReLU(inplace=True)

    def forward(self, x):
        input = x
        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)
        out = self.conv2(out)
        out = self.bn2(out)
        out += input

        return self.relu(out)

    def __call__(self, x):
        return self.forward(x)

class ValueEncoder:
    """Encoder for reward and value targets from Appendix of MuZero Paper."""

    def __init__(self,
                 min_value,
                 max_value,
                 num_steps,
                 use_contractive_mapping=True):
        if not max_value > min_value:
            raise ValueError('max_value must be > min_value')
        min_value = float(min_value)
        max_value = float(max_value)
        if use_contractive_mapping:
            max_value = contractive_mapping(max_value)
            min_value = contractive_mapping(min_value)
        if num_steps <= 0:
            num_steps = np.ceil(max_value) + 1 - np.floor(min_value)
        self.min_value = min_value
        self.max_value = max_value
        self.value_range = max_value - min_value
        self.num_steps = num_steps
        self.step_size = self.value_range / (num_steps - 1)
        self.step_range_int = np.arange(0, self.num_steps, dtype=int)
        self.step_range_float = self.step_range_int.astype(float)
        self.use_contractive_mapping = use_contractive_mapping

    def encode(self, value):  # not worth optimizing
        if len(value.shape) != 1:
            raise ValueError(
                'Expected value to be 1D Tensor [batch_size], but got {}.'.format(
                    value.shape))
        if self.use_contractive_mapping:
            value = contractive_mapping(value)
        value = np.expand_dims(value, -1)
        clipped_value = np.clip(value, self.min_value, self.max_value)
        above_min = clipped_value - self.min_value
        num_steps = above_min / self.step_size
        lower_step = np.floor(num_steps)
        upper_mod = num_steps - lower_step
        lower_step = lower_step.astype(int)
        upper_step = lower_step + 1
        lower_mod = 1.0 - upper_mod
        lower_encoding, upper_encoding = (
            np.equal(step, self.step_range_int).astype(float) * mod
            for step, mod in (
                (lower_step, lower_mod),
                (upper_step, upper_mod),)
        )
        return lower_encoding + upper_encoding

    def decode(self, logits):  # not worth optimizing
        if len(logits.shape) != 2:
            raise ValueError(
                'Expected logits to be 2D Tensor [batch_size, steps], but got {}.'
                .format(logits.shape))
        num_steps = np.sum(logits * self.step_range_float, -1)
        above_min = num_steps * self.step_size
        value = above_min + self.min_value
        if self.use_contractive_mapping:
            value = inverse_contractive_mapping(value)
        return value


# From the MuZero paper.
def contractive_mapping(x, eps=0.001):
    return np.sign(x) * (np.sqrt(np.abs(x) + 1.) - 1.) + eps * x


# From the MuZero paper.
def inverse_contractive_mapping(x, eps=0.001):
    return np.sign(x) * \
           (np.square((np.sqrt(4 * eps * (np.abs(x) + 1. + eps) + 1.) - 1.) / (2. * eps)) - 1.)

# Softmax function in np because we're converting it anyway
def softmax_stable(x):
    return np.exp(x - np.max(x)) / np.exp(x - np.max(x)).sum()

