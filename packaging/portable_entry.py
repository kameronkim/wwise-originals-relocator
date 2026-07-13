from multiprocessing import freeze_support

from wwise_p4_source_relocator.gui import main


if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
