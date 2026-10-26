import asyncio
from voice_support.bootstrap import main
if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('\nSession ended.')
