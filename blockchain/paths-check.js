const { ethers } = require('ethers');
const m = 'candy maple cake sugar pudding cream honey rich smooth crumble sweet treat';
const paths = [
  "m/44'/60'/0'/0/0",
  "m/44'/60'/0'/0/1",
  "m/44'/60'/0'/0/2",
  "m/44'/60'/0'/0/3",
];
paths.forEach((p) => {
  const w = ethers.Wallet.fromMnemonic(m, p);
  console.log(p, '->', w.address, '| priv:', w.privateKey);
});
console.log('Ganache account[0]: 0x90F8bf6A479f320ead074411a4B0e7944Ea8c9C1');
console.log('Ganache account[1]: 0xFFcf8FDEE72ac11b5c542428B35EEF5769C409f0');
console.log('Ganache account[2]: 0x22d491Bde2303f2f43325b2108D26f1eAbA1e32b');

