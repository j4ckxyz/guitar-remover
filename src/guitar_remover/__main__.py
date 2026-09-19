import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from guitar_remover.app import main
    sys.exit(main())
