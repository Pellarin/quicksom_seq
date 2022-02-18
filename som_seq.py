#!/usr/bin/env python3
# -*- coding: UTF8 -*-

#############################################################################
# Author: Guillaume Bouvier -- guillaume.bouvier@pasteur.fr                 #
# https://research.pasteur.fr/en/member/guillaume-bouvier/                  #
# Copyright (c) 2022 Institut Pasteur                                       #
#                 				                            #
#                                                                           #
#  Redistribution and use in source and binary forms, with or without       #
#  modification, are permitted provided that the following conditions       #
#  are met:                                                                 #
#                                                                           #
#  1. Redistributions of source code must retain the above copyright        #
#  notice, this list of conditions and the following disclaimer.            #
#  2. Redistributions in binary form must reproduce the above copyright     #
#  notice, this list of conditions and the following disclaimer in the      #
#  documentation and/or other materials provided with the distribution.     #
#  3. Neither the name of the copyright holder nor the names of its         #
#  contributors may be used to endorse or promote products derived from     #
#  this software without specific prior written permission.                 #
#                                                                           #
#  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS      #
#  "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT        #
#  LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR    #
#  A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT     #
#  HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,   #
#  SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT         #
#  LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,    #
#  DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY    #
#  THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT      #
#  (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE    #
#  OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.     #
#                                                                           #
#  This program is free software: you can redistribute it and/or modify     #
#                                                                           #
#############################################################################

import sys
import re
import os
import dill as pickle  # For tricky pickles
# import pickle
import quicksom.som
from Bio.SubsMat import MatrixInfo
import numpy as np
import torch


def read_fasta(fastafilename, names=None):
    """
    """
    sequences = []
    seq = None
    seqname = None
    seqnames = []
    with open(fastafilename) as fastafile:
        for line in fastafile:
            if line[0] == ">":  # sequence name
                if seq is not None:
                    if names is None or seqname in names:
                        seqnames.append(seqname)
                        sequences.append(seq)
                seqname = line[1:].strip()
                seq = ''
            else:
                seq += line.strip()
    if names is None or seqname in names:
        sequences.append(seq)
        seqnames.append(seqname)
    return seqnames, sequences


# add a character for gap opening
def _substitute_opening_gap_char(seq):
    rex = re.compile('[A-Z]-')
    newseq = list(seq)
    if newseq[0] == "-":
        newseq[0] = "|"
    iterator = rex.finditer(seq)
    for match in iterator:
        try:
            newseq[match.span()[1] - 1] = "|"
        except:
            continue
    return "".join(newseq)


# transform a sequence to a vector
def seq2vec(sequence, dtype='prot'):
    """
    - sequence: string
    """
    aalist = list('ABCDEFGHIKLMNPQRSTVWXYZ|-')
    nucllist = list('ATGCSWRYKMBVHDN|-')
    if dtype == 'prot':
        mapper = dict([(r, i) for i, r in enumerate(aalist)])
        naa_types = len(aalist)
    elif dtype == 'nucl':
        mapper = dict([(r, i) for i, r in enumerate(nucllist)])
        naa_types = len(nucllist)
    else:
        raise ValueError("dtype must be 'prot' or 'nucl'")
    sequence = _substitute_opening_gap_char(sequence)
    naa = len(sequence)
    vec = np.zeros((naa, naa_types))
    for i, res in enumerate(list(sequence)):
        ind = mapper[res]
        vec[i, ind] = 1.
    return vec


def vectorize(sequences, dtype='prot'):
    vectors = np.asarray([seq2vec(s, dtype).flatten() for s in sequences])
    return vectors


def get_blosum62():
    aalist = list('ABCDEFGHIKLMNPQRSTVWXYZ|-')
    b62 = np.zeros((23, 23))
    for k in MatrixInfo.blosum62:
        i0 = aalist.index(k[0])
        i1 = aalist.index(k[1])
        b62[i0, i1] = MatrixInfo.blosum62[k]
        b62[i1, i0] = MatrixInfo.blosum62[k]
    return b62


def torchify(x):
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    x = torch.from_numpy(x)
    x = x.to(device)
    x = x.float()
    return x


def score_matrix_vec(vec1, vec2, dtype="prot", gap_s=-5, gap_e=-1, b62=None, NUC44=None):
    """
    PyTorch Implementation
    """
    if dtype == 'prot':
        matrix = b62
    elif dtype == 'nucl':
        matrix = NUC44
    else:
        raise ValueError("dtype must be 'prot' or 'nucl'")
    device1 = vec1.device.type
    device2 = matrix.device.type
    if device1 != device2:
        matrix = matrix.to(device1)
    if vec1.ndim == 2:
        vec1 = vec1[None, ...]
    if vec2.ndim == 2:
        vec2 = vec2[None, ...]
    matv2 = torch.matmul(matrix[None, ...], torch.swapaxes(vec2[..., :-2], 1, 2))
    scores = torch.einsum('aij,bji->ab', vec1[..., :-2], matv2)
    for i in range(len(vec1)):
        # scores.shape = (a, b) with a: size of batch and b size of SOM
        scores[i] += torch.maximum(vec1[i, ..., -2], vec2[..., -2]).sum(axis=1) * gap_s
        scores[i] += torch.maximum(vec1[i, ..., -1], vec2[..., -1]).sum(axis=1) * gap_e
    # scores = list(scores.to('cpu').numpy())
    if len(scores) == 1:
        return scores[0]
    else:
        return scores


def seqmetric(seqs1, seqs2, b62):
    nchar = 25
    batch_size = seqs1.shape[0]
    seqlenght = seqs1.shape[-1] // nchar
    n2 = seqs2.shape[0]
    seqs1 = seqs1.reshape((batch_size, seqlenght, nchar))
    seqs2 = seqs2.reshape((n2, seqlenght, nchar))
    scores = score_matrix_vec(seqs1, seqs2, b62=b62)
    return -scores


def main(ali, batch_size, somside, nepochs, alpha, sigma, load, periodic, scheduler, nrun, outname, doplot, plot_ext):
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print('Running on', device)

    dtype = 'prot'

    b62 = get_blosum62()
    b62 = torchify(b62)

    seqnames, sequences = read_fasta(ali)
    seqnames = np.asarray(seqnames)
    inputvectors = vectorize(sequences, dtype=dtype)
    n_inp = inputvectors.shape[0]
    print('inputvectors.shape:', inputvectors.shape)
    n, dim = inputvectors.shape
    inputvectors = torchify(inputvectors)
    baseoutname = os.path.splitext(outname)[0]

    if load is not None:
        with open(load, 'rb') as somfile:
            som = pickle.load(somfile)
            print(dir(som))
            # somsize = som.m * som.n
            som.to_device(device)
    else:
        # somsize = somside**2
        som = quicksom.som.SOM(somside,
                               somside,
                               niter=nepochs,
                               dim=dim,
                               alpha=alpha,
                               sigma=sigma,
                               device=device,
                               periodic=periodic,
                               metric=lambda s1, s2: seqmetric(s1, s2, b62),
                               sched=scheduler)
    print('batch_size:', batch_size)
    print('sigma:', som.sigma)
    if som.alpha is not None:
        print('alpha:', som.alpha)
    som.fit(inputvectors,
            batch_size=batch_size,
            do_compute_all_dists=False,
            unfold=False,
            normalize_umat=False,
            nrun=nrun,
            sigma=sigma,
            alpha=alpha,
            logfile=f'{baseoutname}.log')
    print('Computing BMUS')
    som.bmus, som.error = som.predict(inputvectors, batch_size=batch_size)
    index = np.arange(len(som.bmus))
    out_arr = np.zeros(n_inp, dtype=[('bmu1', int), ('bmu2', int), ('error', float), ('index', int), ('label', 'U512')])
    out_arr['bmu1'] = som.bmus[:, 0]
    out_arr['bmu2'] = som.bmus[:, 1]
    out_arr['error'] = som.error
    out_arr['index'] = index
    out_arr['label'] = seqnames
    out_fmt = ['%d', '%d', '%.4g', '%d', '%s']
    out_header = '#bmu1 #bmu2 #error #index #label'
    np.savetxt(f"{baseoutname}_bmus.txt", out_arr, fmt=out_fmt, header=out_header, comments='')
    som.to_device('cpu')
    if doplot:
        import matplotlib.pyplot as plt
        plt.matshow(som.umat)
        plt.colorbar()
        plt.savefig(f'{baseoutname}_umat.{plot_ext}')
    pickle.dump(som, open(outname, 'wb'))


if __name__ == '__main__':
    import argparse
    # argparse.ArgumentParser(prog=None, usage=None, description=None, epilog=None, parents=[], formatter_class=argparse.HelpFormatter, prefix_chars='-', fromfile_prefix_chars=None, argument_default=None, conflict_handler='error', add_help=True, allow_abbrev=True, exit_on_error=True)
    parser = argparse.ArgumentParser(description='')
    # parser.add_argument(name or flags...[, action][, nargs][, const][, default][, type][, choices][, required][, help][, metavar][, dest])
    parser.add_argument('-a', '--aln', help='Alignment file', required=True)
    parser.add_argument('-b', '--batch', help='Batch size (default: 100)', default=100, type=int)
    parser.add_argument('--somside', help='Size of the side of the square SOM', default=50, type=int)
    parser.add_argument('--alpha', help='learning rate', default=None, type=float)
    parser.add_argument('--sigma', help='Learning radius for the SOM', default=None, type=float)
    parser.add_argument('--nepochs', help='Number of SOM epochs', default=2, type=int)
    parser.add_argument("-o", "--out_name", default='som.p', help="name of pickle to dump (default som.p)")
    parser.add_argument('--noplot', help='Do not plot the resulting U-matrix', action='store_false', dest='doplot')
    parser.add_argument('--plot_ext', help='Filetype extension for the U-matrix plot (default: pdf)', default='pdf')
    parser.add_argument('--periodic', help='Periodic toroidal SOM', action='store_true')
    parser.add_argument('--scheduler',
                        help='Which scheduler to use, can be linear, exp or half (exp by default)',
                        default='exp')
    parser.add_argument('--load', help='Load the given som pickle file and use it as starting point for a new training')
    parser.add_argument('--nrun',
                        help='Number of run that will be run. Useful to compute the scheduler decay.',
                        type=int,
                        default=1)
    args = parser.parse_args()

    main(ali=args.aln,
         batch_size=args.batch,
         somside=args.somside,
         nepochs=args.nepochs,
         alpha=args.alpha,
         sigma=args.sigma,
         load=args.load,
         periodic=args.periodic,
         scheduler=args.scheduler,
         nrun=args.nrun,
         outname=args.out_name,
         doplot=args.doplot,
         plot_ext=args.plot_ext)
