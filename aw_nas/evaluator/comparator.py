from pygcn.layers import GraphConvolution
import torch
import torch.nn as nn
import numpy as np
from torch.autograd import Variable
import scipy.sparse as sp
import torch.nn.functional as F
from aw_nas import utils


def get_variable(inputs, device, **kwargs):
	if type(inputs) in [list, np.ndarray]:
		inputs = torch.tensor(inputs)
		# TODO: Variable is deprecated
		out = Variable(inputs.to(device), **kwargs)
		return out


def sparse_mx_to_torch_sparse_tensor(sparse_mx):
	"""Convert a scipy sparse matrix to a torch sparse tensor."""
	sparse_mx = sparse_mx.tocoo().astype(np.float32)
	indices = torch.from_numpy(
		np.vstack((sparse_mx.row, sparse_mx.col)).astype(np.int64))
	values = torch.from_numpy(sparse_mx.data)
	shape = torch.Size(sparse_mx.shape)
	return torch.sparse.FloatTensor(indices, values, shape)


class GCNComparator(object):
	def __init__(self, search_space, op_dim, op_hid, gcn_out_dim, dropout, device, training=True):
		self.search_space = search_space
		self.op_dim = op_dim
		self.op_hid = op_hid
		self.gcn_out_dim = gcn_out_dim
		self.device = device
		self.dropout = dropout
		self.training = training
		self.gcn1 = GraphConvolution(op_hid, gcn_out_dim)
		self.gcn2 = GraphConvolution(gcn_out_dim, gcn_out_dim)

		# the first two nodes
		self.init_node_emb = nn.Embedding(2, 2 * op_dim)

		self.op_emb = nn.Embedding(len(search_space.shared_primitives), op_dim)
		# [op0, op1]
		self.x_hidden = nn.Linear(2 * op_dim, op_hid)

		self.score_fc = nn.Linear(2 * gcn_out_dim, 1)


		# self.optimizer = utils.init_optimizer()

	def compare(self, arch_1, arch_2):

		s_1 = self.arch_scoring(arch_1)
		s_2 = self.arch_scoring(arch_2)
		return 1 if s_1 > s_2 else 0

	def pairwise_loss(self, arch_1, arch_2, better, margin=0.01):
		s_1 = self.arch_scoring(arch_1)
		s_2 = self.arch_scoring(arch_2)
		if better:
			loss = torch.max(torch.tensor(0.0), margin - (s_1 - s_2))
		else:
			loss = torch.max(torch.tensor(0.0), margin - (s_2 - s_1))
		return loss

	def arch_scoring(self, arch):
		"""

		:param arch: {normal_cell: [prev_nodes,prev_ops]},
					  reduce_cell: [prev_nodes,prev_ops]}}
		:return: score, scalar
		"""
		x_n = self.get_x(arch['normal'][1])
		adj_n = self.get_adj(arch['normal'][0], self.search_space.num_steps + self.search_space.num_node_inputs)
		x_r = self.get_x(arch['reduce'][1])
		adj_r = self.get_adj(arch['reduce'][0], self.search_space.num_steps + self.search_space.num_node_inputs)
		x_n = F.relu(self.gcn1(x_n, adj_n))
		x_n = F.dropout(x_n, self.dropout, training=self.training)
		x_n = self.gcn2(x_n, adj_n)
		x_n = x_n[2:]
		y_n = torch.mean(x_n, dim=0)

		x_r = F.relu(self.gcn1(x_r, adj_r))
		x_r = F.dropout(x_r, self.dropout, training=self.training)
		x_r = self.gcn2(x_r, adj_r)
		x_r = x_r[2:]
		y_r = torch.mean(x_r, dim=0)

		# concatenate y_r, y_n
		y = torch.cat((y_n, y_r))
		score = self.score_fc(y)
		return score


	def get_x(self, arch):

		# initial the first two nodes
		op0_list = []
		op1_list = []
		for idx, op in enumerate(arch):
			if idx % 2 == 0:
				op0_list.append(op)
			else:
				op1_list.append(op)
		assert len(op0_list) == len(op1_list), 'inconsistent size between op0_list and op1_list'
		init_node_list = get_variable(list(range(0, 2, 1)), self.device, requires_grad=False)
		op0_list = get_variable(op0_list, self.device, requires_grad=False)
		op1_list = get_variable(op1_list, self.device, requires_grad=False)
		emb_init_node = self.init_node_emb(init_node_list)
		emb_op0 = self.op_emb(op0_list)
		emb_op1 = self.op_emb(op1_list)
		emb_op = torch.cat((emb_op0, emb_op1), dim=1)
		emb_x = torch.cat((emb_init_node, emb_op), dim=0)
		x = self.x_hidden(emb_x)
		return x

	def get_adj(self, arch, num_node):
		"""

		:param arch: previous_nodes, e.g. [1, 0, 0, 1, 2, 0, 4, 4], 0,1 is the previous init nodes
		:param num_node:
		:return:
		"""
		f_nodes = np.array(arch)
		t_nodes = np.repeat(np.array(range(num_node)), self.search_space.num_node_inputs)
		print(arch)
		adj = sp.coo_matrix((np.ones(f_nodes.shape[0]), (t_nodes, f_nodes)), shape=(num_node, num_node), dtype=np.float32)
		adj = adj.multiply(adj > 0)
		# build symmetric adjacency matrix
		# adj = adj + adj.T.multiply(adj.T > adj) - adj.multiply(adj.T > adj)
		adj = sparse_mx_to_torch_sparse_tensor(adj)
		return adj

	def update(self, arch_1, arch_2, better):
		loss = self.pairwise_loss(arch_1, arch_2, better)
		loss.backward()
		self.optimizer.step()
		return loss.detach()



