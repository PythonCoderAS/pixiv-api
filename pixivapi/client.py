from json import JSONDecodeError

from requests import RequestException, Session

from pixivapi.common import (
    HEADERS,
    Struct,
    format_bool,
    parse_qs,
    require_auth,
)
from pixivapi.enums import (
    ContentType,
    RankingMode,
    SearchTarget,
    Sort,
    Visibility,
)
from pixivapi.errors import BadApiResponse, LoginError
from pixivapi.models import Comment, Illustration

AUTH_URL = 'https://oauth.secure.pixiv.net/auth/token'
BASE_URL = 'https://app-api.pixiv.net'
FILTER = 'for_ios'


class Client:
    """
    A client for the Pixiv API.

    :ivar str language: The language tag translations should be in.
    :ivar str client_id: The client ID. Typically, leaving this as
        default is ok.
    :ivar str client_secret: The client secret. Typically, leaving
        this as default is ok.
    :ivar dict account: Basic details of the logged in account.
    :ivar str access_token: The access token used to authorize requests.
    :ivar str refresh_token: The refresh token used to obtain new access
        tokens.
    :ivar requests.Session session: The requests session.
    """

    def __init__(
        self,
        language='English',
        client_id='KzEZED7aC0vird8jWyHM38mXjNTY',
        client_secret='W9JZoJe00qPvJsiyCGT3CCtC6ZUtdpKpzMbNlUGP',
    ):
        self.language = language
        self.client_id = client_id
        self.client_secret = client_secret

        self.account = None
        self.access_token = None
        self.refresh_token = None

        self.session = Session()
        self.session.headers.update(HEADERS)
        if self.language:
            self.session.headers.update({'Accept-Language': self.language})

    def _request_json(
        self,
        method,
        url,
        params=None,
        headers=None,
        data=None,
    ):
        """
        A wrapper for JSON requests.
        """
        try:
            response = self.session.request(
                method=method,
                url=url,
                params=params,
                headers=headers,
                data=data,
            )
            if response.status_code // 100 == 4:
                raise BadApiResponse(
                    f'Status code: {response.status_code}', response.text
                )
            return response.json()
        except JSONDecodeError as e:
            raise BadApiResponse from e

    def download(self, url, destination, referer='https://pixiv.net'):
        """
        Download a file to a given destination. This method uses
        the client's access token if available.

        :param str url:     The URL to the file.
        :param destination: The destination file. Must be writeable.
        :param str referer: The Referer header.

        :raises FileNotFoundError: If the destination's directory does
            not exist.
        :raises PermissionError: If the destination cannot be written to.
        """
        response = self.session.request(
            method='get',
            url=url,
            headers={'Referer': referer},
            stream=True,
        )
        with destination.open('wb') as f:
            for chunk in response.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)

    def login(self, username, password):
        """
        Log in with username and password to fetch an access token
        and a refresh token. Assigns the tokens to instance variables.

        :param str username: Your username.
        :param str password: Your password.

        :raises LoginError: If login fails.
        """
        self._make_auth_request(
            {
                'grant_type': 'password',
                'username': username,
                'password': password,
            }
        )

    def authenticate(self, refresh_token):
        """
        Use a refresh token to obtain a new access token. Assigns
        both tokens to instance variables.

        :param str refresh_token: The refresh token.

        :raises LoginError: If authentication fails.
        """
        self._make_auth_request(
            {
                'grant_type': 'refresh_token',
                'refresh_token': refresh_token,
            }
        )

    def _make_auth_request(self, data):
        try:
            r = self.session.request(
                method='post',
                url=AUTH_URL,
                data={
                    'client_id': self.client_id,
                    'client_secret': self.client_secret,
                    'get_secure_url': 1,
                    **data,
                },
            ).json()
            self.account = Struct(r['response']['user'])
            self.access_token = r['response']['access_token']
            self.refresh_token = r['response']['refresh_token']

            self.session.headers.update(
                {'Authorization': f'Bearer {self.access_token}'}
            )
        except (RequestException, JSONDecodeError, KeyError) as e:
            raise LoginError from e

    @require_auth
    def search_illustrations(
        self,
        word,
        search_target=SearchTarget.TAGS_PARTIAL,
        sort=Sort.DATE_DESC,
        duration=None,
        offset=None,
    ):
        """
        Search the illustrations. A maximum of 30 illustrations are
        returned in one response.

        :param str word: The search term.
        :param SearchTarget search_target: The target for the search term.
        :param Sort sort: How to sort the illustrations.
        :param Duration duration: An optional max-age for the illustrations.
        :param int offset: The number of illustrations to offset by.

        :return: A dictionary containing the searched illustrations, the
            offset for the next page of search images (``None`` if there
            is no next page), and the search span limit.

        .. code-block:: python

           {
               'illustrations': [Illustration, ...],  # List of illustrations.
               'next': 30,  # Offset to get the next page of illustrations.
               'search_span_limit': 31536000,
           }

        :rtype: dict

        :raises requests.RequestException: If the request fails.
        :raises BadApiResponse: If the response is not valid JSON.
       """
        response = self._request_json(
            method='get',
            url=f'{BASE_URL}/v1/search/illust',
            params={
                'word': word,
                'search_target': search_target.value,
                'sort': sort.value,
                'duration': duration.value if duration else None,
                'offset': offset,
                'filter': FILTER,
            },
        )

        return {
            'illustrations': [
                Illustration(**illust, client=self)
                for illust in response['illusts']
            ],
            'next': parse_qs(response['next_url'], param='offset'),
            'search_span_limit': response['search_span_limit'],
        }

    @require_auth
    def fetch_illustration(self, illustration_id):
        """
        Fetch the details of a single illustration.

        :param int illustration_id: The ID of the illustration.

        :return: An illustration object.
        :rtype: Illustration

        :raises requests.RequestException: If the request fails.
        :raises BadApiResponse: If the response is not valid JSON.
        """
        return Illustration(
            **self._request_json(
                method='get',
                url=f'{BASE_URL}/v1/illust/detail',
                params={
                    'illust_id': illustration_id,
                }
            )['illust'],
            client=self,
        )

    @require_auth
    def fetch_illustration_comments(
        self,
        illustration_id,
        offset=None,
        include_total_comments=False,
    ):
        """
        Fetch the comments of an illustration. A maximum of 30 comments
        are returned in one response.

        **Note:** The ``total_comments`` key does not equal the number
        of comments that will be returned by the API. If requesting all
        the comments, use the ``next`` key to determine whether or not
        to continue, not the ``total_comments`` key.

        :param int illustration_id: ID of the illustration.
        :param int offset: Number of comments to offset by.
        :param bool include_total_comments: Whether or not to include a
            the total number of comments on the illustration. If set to
            False, the ``total_comments`` key in the response will be ``0``.

        :return: A dictionary containing the comments, the offset for the
            next page of comments, and the total number of comments.

        .. code-block:: python

           {
               'comments': [Comment, ...],  # List of comments.
               'next': 30,  # Offset to get the next page of comments.
               'total_comments': 142,
           }

        :rtype: dict

        :raises requests.RequestException: If the request fails.
        :raises BadApiResponse: If the response is not valid JSON.
        """
        response = self._request_json(
            method='get',
            url=f'{BASE_URL}/v1/illust/comments',
            params={
                'illust_id': illustration_id,
                'offset': offset,
                'include_total_comments': format_bool(include_total_comments),
            },
        )

        return {
            'comments': [
                Comment(**comment, client=self)
                for comment in response['comments']
            ],
            'next': parse_qs(response['next_url'], param='offset'),
            'total_comments': response['total_comments'],
        }

    @require_auth
    def fetch_illustration_related(self, illustration_id, offset=None):
        """
        Fetch illustrations related to a specified illustration. A
        maximum of TODO are returned in one response.

        :param int illustration_id: ID of the illustration.
        :param int offset: Illustrations to offset by.

        :return: A dictionary containing the related illustrations and
            the offset for the next page of illustrations.

        .. code-block:: python

           {
               'illustrations': [Illustration, ...],  # List of illustrations.
               'next': 30,  # Offset to get the next page of illustrations.
           }

        :rtype: dict

        :raises requests.RequestException: If the request fails.
        :raises BadApiResponse: If the response is not valid JSON.
        """
        response = self._request_json(
            method='get',
            url=f'{BASE_URL}/v2/illust/related',
            params={
                'illust_id': illustration_id,
                'offset': offset,
            },
        )

        return {
            'illustrations': [
                Illustration(**illust, client=self)
                for illust in response['illusts']
            ],
            'next': parse_qs(response['next_url'], param='offset'),
        }

    @require_auth
    def fetch_illustrations_following(
        self,
        visibility=Visibility.PUBLIC,
        offset=None,
    ):
        """
        Fetch new illustrations from followed artists. A maximum of 30
        illustrations are returned in one response.

        :param Visibility visibility: Visibility of the followed artist;
            ``PUBLIC`` if publicly followed; ``PRIVATE`` if privately.
        :param int offset: The number of illustrations to offset by.

        :return: A dictionary containing the new illustrations and
            the offset for the next page of illustrations.

        .. code-block:: python

           {
               'illustrations': [Illustration, ...],  # List of illustrations.
               'next': 30,  # Offset to get the next page of illustrations.
           }

        :rtype: dict

        :raises requests.RequestException: If the request fails.
        :raises BadApiResponse: If the response is not valid JSON.
        """
        response = self._request_json(
            method='get',
            url=f'{BASE_URL}/v2/illust/follow',
            params={
                'restrict': visibility.value,
            },
        )

        return {
            'illustrations': [
                Illustration(**illust, client=self)
                for illust in response['illusts']
            ],
            'next': parse_qs(response['next_url'], param='offset'),
        }

    @require_auth
    def fetch_illustrations_recommended(
        self,
        content_type=ContentType.ILLUSTRATION,
        include_ranking_illustrations=False,
        max_bookmark_id_for_recommend=None,
        min_bookmark_id_for_recent_illustrations=None,
        offset=None,
        bookmark_illust_ids=None,
        include_ranking_label=True,
    ):
        """
        Fetch one's recommended illustrations.

        :param ContentType content_type: The type of content to fetch.
        :param bool include_ranking_illustrations: If ``True``, the top
            10 ranked illustrations daily are included in the response.
            If False, the ``ranking_illustrations`` key in the response
            dict will be empty.
        :param int max_bookmark_id_for_recommend: The maximum bookmark
            ID for recommended illustrations, used for changing the
            returned illustrations.
        :param int min_bookmark_id_for_recent_illustrations: The minimum
            bookmark ID for recent illustrations, used for changing
            the returned illustrations.
        :param int offset: The number of illustrations to offset by.
        :param list bookmark_illust_ids: A list of illustration IDs.
        :param bool include_ranking_label: Whether or not to include
            the ranking label.

        :return: A dictionary containing the recommended illustrations and
            the parameters for the next page of illustrations.

        .. code-block:: python

           {
               'contest_exists': False,  # Does a contest exist?
               'illustrations': [Illustration, ...],  # List of illustrations.
               'next': {  # Parameters to get the next set of illustrations.
                   'min_bookmark_id_for_recent_illustrations': 6277740037,
                   'max_bookmark_id_for_recommend': 6268205545,
                   'offset': 0,
               },
               'ranking_illustrations': [Illustration, ...] # Ranking illust.
           }

        :rtype: dict

        :raises requests.RequestException: If the request fails.
        :raises BadApiResponse: If the response is not valid JSON.
        """
        if bookmark_illust_ids:
            bookmark_illust_ids = ','.join(
                str(bid) for bid in bookmark_illust_ids
            )

        response = self._request_json(
            method='get',
            url=f'{BASE_URL}/v1/illust/recommended',
            params={
                'content_type': content_type.value,
                'include_ranking_label': format_bool(include_ranking_label),
                'max_bookmark_id_for_recommend': max_bookmark_id_for_recommend,
                'min_bookmark_id_for_recent_illust': (
                    min_bookmark_id_for_recent_illustrations,
                ),
                'offset': offset,
                'include_ranking_illusts': format_bool(
                    include_ranking_illustrations
                ),
                'bookmark_illust_ids': bookmark_illust_ids,
            },
        )
        return {
            'contest_exists': response['contest_exists'],
            'illustrations': [
                Illustration(**illust, client=self)
                for illust in response['illusts']
            ],
            'next': {
                'min_bookmark_id_for_recent_illustrations': (
                    parse_qs(
                        response['next_url'],
                        param='min_bookmark_id_for_recent_illust',
                    )
                ),
                'max_bookmark_id_for_recommend': (
                    parse_qs(
                        response['next_url'],
                        param='max_bookmark_id_for_recommend',
                    )
                ),
                'offset': parse_qs(response['next_url'], param='offset'),
            },
            'ranking_illustrations': [
                Illustration(**illust, client=self)
                for illust in response['ranking_illusts']
            ],
        }

    @require_auth
    def fetch_illustrations_ranking(
        self,
        mode=RankingMode.DAY,
        date=None,
        offset=None,
    ):
        """
        Fetch the ranking illustrations. A maximum of TODO are returned
        in one response.

        :param RankingMode mode: The ranking list to fetch.
        :param str date: The date of the list, in ``%Y-%m-%d`` format.
        :param int offset: The number of illustrations to offset by.

        :return: A dictionary containing the ranking illustrations and
            the offset for the next page of illustrations.

        .. code-block:: python

           {
               'illustrations': [Illustration, ...],  # List of illustrations.
               'next': 30,  # Offset to get the next page of illustrations.
           }

        :rtype: dict

        :raises requests.RequestException: If the request fails.
        :raises BadApiResponse: If the response is not valid JSON.
        """
        response = self._request_json(
            method='get',
            url=f'{BASE_URL}/v1/illust/ranking',
            params={
                'mode': mode.value,
                'date': date,
                'offset': offset,
                'filter': FILTER,
            },
        )

        return {
            'illustrations': [
                Illustration(**illust, client=self)
                for illust in response['illusts']
            ],
            'next': parse_qs(response['next_url'], param='offset'),
        }

    @require_auth
    def fetch_trending_tags(self):
        """
        Fetch trending illustrations and tags.

        :return: A list of dicts containing an illustration, the
            tag name, and the tag translation.

        .. code-block:: python

           [
               {
                   'illustration': Illustration,
                   'tag': '艦これ',
                   'translated_name': 'Kancolle',
               },
               ...
           ]

        :rtype: dict

        :raises requests.RequestException: If the request fails.
        :raises BadApiResponse: If the response is not valid JSON.
        """
        response = self._request_json(
            method='get',
            url=f'{BASE_URL}/v1/trending-tags/illust',
            params={
                'filter': FILTER,
            },
        )

        return [
            {
                'illustration': Illustration(**trending['illust']),
                'tag': trending['tag'],
                'translated_name': trending['translated_name'],
            }
            for trending in response['trend_tags']
        ]

    @require_auth
    def fetch_bookmark(self, illustration_id):
        """
        Fetch details about a bookmarked illustration.

        :param int illustration_id: The ID of the bookmarked illustration.

        :return: A dictionary containing whether or not the illustration is
            bookmarked, the visibility of the bookmark, and a list of tags.

        .. code-block:: python

           {
               'is_bookmarked': True,
               'visibility': Visibility.PUBLIC,
               'tags': [
                   {
                       'is_registered': False,
                       'name': 'ghostblade',
                   },
                   ...
               ],
           }

        :rtype: dict

        :raises requests.RequestException: If the request fails.
        :raises BadApiResponse: If the response is not valid JSON.
        """
        response = self._request_json(
            method='get',
            url=f'{BASE_URL}/v2/illust/bookmark/detail',
            params={
                'illust_id': illustration_id,
            },
        )['bookmark_detail']

        return {
            'is_bookmarked': response['is_bookmarked'],
            'visibility': Visibility(response['restrict']),
            'tags': response['tags'],
        }

    @require_auth
    def add_bookmark(
        self,
        illustration_id,
        visibility=Visibility.PUBLIC,
    ):
        """
        Bookmark an illustration.

        :param int illustration_id: The ID of the illustration.
        :param Visibility visibility: The visibility of the bookmark.
        """
        # TODO: Figure out correct way to serialize tags.
        self._request_json(
            method='post',
            url=f'{BASE_URL}/v2/illust/bookmark/add',
            data={
                'illust_id': illustration_id,
                'restrict': visibility.value,
            },
        )

    @require_auth
    def delete_bookmark(self, illustration_id):
        """
        Delete a bookmark.

        :param int illustration_id: The ID of the illustration.
        """
        self._request_json(
            method='post',
            url=f'{BASE_URL}/v1/illust/bookmark/delete',
            data={
                'illust_id': illustration_id,
            },
        )

    def fetch_user(self, user_id):
        """
        Fetch details about a Pixiv user.

        :param int user_id: The ID of the user.
        """
        return Struct(
            self._request_json(
                method='get',
                url=f'{BASE_URL}/v1/user/detail',
                params={
                    'user_id': user_id,
                    'filter': FILTER,
                },
            )
        )

    def fetch_user_illustrations(
        self,
        user_id,
        type_=ContentType.ILLUSTRATION,
        offset=None,
    ):
        """
        Fetch the illustrations posted by a user.

        :param int user_id: The ID of the user.
        :param ContentType type_: The type of content to fetch.
        :param int offset: The number of illustrations/manga to offset by.
        """
        # TODO: Check to see if 'illust' includes manga. If not, try None.
        return Struct(
            self._request_json(
                method='get',
                url=f'{BASE_URL}/v1/user/illusts',
                params={
                    'user_id': user_id,
                    'type': type_.value if type_ else None,
                    'offset': offset,
                    'filter': FILTER,
                },
            )
        )

    @require_auth
    def fetch_user_bookmarks(
        self,
        user_id,
        visibility=Visibility.PUBLIC,
        max_bookmark_id=None,
        tag=None,
    ):
        """
        Fetch the illustrations bookmarked by a user. A maximum of TODO
        illustrations are returned in a response.

        :param int user_id: The ID of the user.
        :param Visibility visibility: The visibility of the bookmarks.
            Applies only to requests for one's own bookmarks.
        :param int max_bookmark_id: The ID of the maximum bookmark,
            similar to ``offset`` for other endpoints.
        :param str tag: The bookmark tag to filter bookmarks by. These tags
            can be fetched from ``Client.fetch_user_bookmark_tags``.
        """
        return Struct(
            self._request_json(
                method='get',
                url=f'{BASE_URL}/v1/user/bookmarks/illust',
                params={
                    'user_id': user_id,
                    'restrict': visibility.value,
                    'max_bookmark_id': max_bookmark_id,
                    'tag': tag,
                },
            )
        )

    @require_auth
    def fetch_user_bookmark_tags(
        self,
        visibility=Visibility.PUBLIC,
        offset=None,
    ):
        """
        Fetch the bookmark tags that belong to the user. A maximum of
        TODO tags are returned in a response.

        :param Visibility visibility: The visibility of the tags.
        :param int offset: The number of tags to offset by.
        """
        return Struct(
            self._request_json(
                method='get',
                url=f'{BASE_URL}/v1/user/bookmark-tags/illust',
                params={
                    'restrict': visibility.value,
                    'offset': offset,
                },
            )
        )

    @require_auth
    def fetch_following(
        self,
        user_id,
        visibility=Visibility.PUBLIC,
        offset=None,
    ):
        """
        Fetch the users that a user is following. A maximum of TODO
        users are returned in a response.

        :param int user_id: The ID of the user.
        :param Visibility visibility: The visibility of the followed
            users. Applies only when fetching one's own followed users.
        :param int offset: The number of users to offset by.
        """
        return Struct(
            self._request_json(
                method='get',
                url=f'{BASE_URL}/v1/user/following',
                params={
                    'user_id': user_id,
                    'restrict': visibility.value,
                    'offset': offset,
                },
            )
        )

    @require_auth
    def fetch_followers(self, user_id, offset=None):
        """
        Fetch the users that are following a user. A maximum of TODO
        users are returned in a response.

        :param int user_id: The ID of the user.
        :param int offset: The number of users to offset by.
        """
        return Struct(
            self._request_json(
                method='get',
                url=f'{BASE_URL}/v1/user/follower',
                params={
                    'user_id': user_id,
                    'offset': offset,
                    'filter': FILTER,
                },
            )
        )

    @require_auth
    def fetch_my_pixiv(self, user_id, offset=None):
        """
        TODO: Figure out what this fetches.

        :param int user_id: The ID of the user.
        :param int offset: The number of TODO to offset by.
        """
        return Struct(
            self._request_json(
                method='get',
                url=f'{BASE_URL}/v2/user/list',
                params={
                    'user_id': user_id,
                    'offset': offset,
                    'filter': FILTER,
                },
            )
        )
