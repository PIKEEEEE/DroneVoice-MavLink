import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/receiver'))
from apply_uart import transform

class UARTTests(unittest.TestCase):
    def test_enable_and_preserve_boot_arguments(self):
        c,k=transform('[pi4]\nenable_uart=0\n','console=serial0,115200 console=tty1 root=PARTUUID=test rootwait quiet\n')
        self.assertIn('[all]\nenable_uart=1',c)
        self.assertNotIn('console=serial0',k)
        self.assertIn('console=tty1',k)
        self.assertIn('root=PARTUUID=test',k)
        self.assertEqual(transform(c,k),(c,k))
    def test_invalid_input(self):
        with self.assertRaises(ValueError): transform('', 'console=tty1\n')
        with self.assertRaises(ValueError): transform('', 'root=abc\nconsole=tty1\n')

if __name__=='__main__': unittest.main()
