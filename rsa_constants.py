"""
Created on Thu Jan  8 10:56:17 2015

@author: richard
"""

RSA100 = 1522605027922533360535618378132637429718068114961380688657908494580122963258952897654000350692006139
RSA100_F1 = 37975227936943673922808872755445627854565536638199
RSA100_F2 = 40094690950920881030683735292761468389214899724061

if __name__ == '__main__':
    assert RSA100_F1 * RSA100_F2 == RSA100